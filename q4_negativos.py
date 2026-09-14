"""
Pregunta 4: Productos con margen negativo y en qué tiendas ocurren.

Margen = ingreso neto - costo de lo vendido, con el costo VIGENTE al
momento de cada venta (merge_asof sobre cost_history).

Decisiones documentadas:
  - Costo histórico, no costo actual: usar el vigente hoy generaría
    pérdidas retroactivas que nunca ocurrieron.
  - `monto` se asume total de línea (no unitario).
  - Devoluciones (E) restan ingreso y costo: el producto vuelve al anaquel.
  - Solo canal físico: el e-commerce no tiene tienda_id.
  - Se excluyen ventas anteriores a la primera fecha de vigencia conocida.
"""

import json
from pathlib import Path

import pandas as pd

RAW = Path(r"C:\Users\ervin\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\LocalState\sessions\ADF274A702466B968037426F7D476870AC57C49F\transfers\2026-37\datos_cafenorte (2)\datos")

TIPOS_VENTA = {"I": 1, "E": -1}
TIPOS_NO_VENTA = {"T", "P", "N"}

pd.set_option("display.width", 220)


# ---------------------------------------------------------------- carga
ventas = pd.read_csv(RAW / "sales.csv", parse_dates=["fecha_hora"])

with open(RAW / "inventory.json", encoding="utf-8") as f:
    erp = json.load(f)

tiendas = pd.DataFrame(erp["tiendas_info"]).set_index("tienda_id")
mapeo = pd.DataFrame(erp["sku_mappings"])[["sku_pos", "sku_erp"]]
productos = pd.DataFrame(erp["catalogo"]["productos"])

if mapeo.sku_pos.duplicated().any():
    raise SystemExit("sku_pos duplicado: el merge multiplicaría ventas.")


# -------------------------------------------------- historial de costos
costos = (productos[["sku_erp", "nombre", "categoria", "cost_history"]]
          .explode("cost_history")
          .reset_index(drop=True))
costos = pd.concat([
    costos[["sku_erp", "nombre", "categoria"]],
    pd.json_normalize(costos.cost_history),
], axis=1)
costos["fecha_vigencia"] = pd.to_datetime(costos["fecha_vigencia"])
costos = costos.sort_values("fecha_vigencia")

print(f"Productos: {productos.sku_erp.nunique()} | "
      f"registros de costo: {len(costos)} | "
      f"vigencias {costos.fecha_vigencia.min().date()} a {costos.fecha_vigencia.max().date()}")


# ------------------------------------------------------ ventas netas
desconocidos = set(ventas.tipo_comprobante) - set(TIPOS_VENTA) - TIPOS_NO_VENTA
if desconocidos:
    raise SystemExit(f"Tipos no contemplados: {sorted(desconocidos)}")

v = ventas[ventas.tipo_comprobante.isin(TIPOS_VENTA)].copy()
signo = v.tipo_comprobante.map(TIPOS_VENTA)
v["unidades"] = v.cantidad * signo
v["ingreso"] = v.monto * signo

filas = len(v)
v = v.merge(mapeo, left_on="sku", right_on="sku_pos", how="left")
assert len(v) == filas, "El merge con el mapeo alteró el número de filas"

sin_mapeo = v.sku_erp.isna()
print(f"Ventas sin equivalente en ERP: {sin_mapeo.mean():.1%} -> excluidas "
      f"(sin costo no hay margen calculable)")
v = v[~sin_mapeo].sort_values("fecha_hora")


# ------------------------------- costo vigente al momento de cada venta
v = pd.merge_asof(
    v, costos[["sku_erp", "fecha_vigencia", "costo_mxn", "proveedor"]],
    left_on="fecha_hora", right_on="fecha_vigencia",
    by="sku_erp", direction="backward",
)

sin_costo = v.costo_mxn.isna()
if sin_costo.any():
    print(f"(!) {sin_costo.sum():,} ventas ({sin_costo.mean():.1%}) anteriores a la "
          "primera vigencia de costo -> excluidas")
    v = v[~sin_costo]

v["costo_total"] = v.unidades * v.costo_mxn
v["margen"] = v.ingreso - v.costo_total


# ------------------------------------------------------------ resultado
por_producto = (v.groupby("sku_erp")
                .agg(unidades=("unidades", "sum"),
                     ingreso=("ingreso", "sum"),
                     costo=("costo_total", "sum"),
                     margen=("margen", "sum"),
                     tiendas=("tienda_id", "nunique"))
                .join(productos.set_index("sku_erp")[["nombre", "categoria"]]))
por_producto["margen_pct"] = por_producto.margen / por_producto.ingreso * 100

negativos = por_producto[por_producto.margen < 0].sort_values("margen")

print(f"\n{'=' * 95}\nPRODUCTOS CON MARGEN NEGATIVO: {len(negativos)} de {len(por_producto)}"
      f"\n{'=' * 95}")
if negativos.empty:
    print("Ningún producto tiene margen negativo agregado.")
else:
    print(negativos[["nombre", "categoria", "unidades", "ingreso",
                     "costo", "margen", "margen_pct", "tiendas"]]
          .round(1).to_string())
    print(f"\nPérdida total: {negativos.margen.sum():,.0f} MXN")

    print(f"\n{'=' * 95}\nDESGLOSE POR TIENDA\n{'=' * 95}")
    for sku in negativos.index:
        info = negativos.loc[sku]
        print(f"\n{info.nombre} ({sku}) | margen {info.margen:,.0f} MXN")
        det = (v[v.sku_erp == sku].groupby("tienda_id")
               .agg(unidades=("unidades", "sum"), ingreso=("ingreso", "sum"),
                    margen=("margen", "sum"))
               .join(tiendas[["ciudad", "region"]])
               .sort_values("margen"))
        det["margen_pct"] = det.margen / det.ingreso * 100
        print(det.head(10).round(1).to_string())

# Productos rentables en agregado pero con pérdida en tiendas puntuales
por_tienda = v.groupby(["sku_erp", "tienda_id"]).margen.sum()
focos = por_tienda[(por_tienda < 0) & (~por_tienda.index.get_level_values(0)
                                       .isin(negativos.index))]
print(f"\n{'=' * 95}\nPRODUCTOS RENTABLES CON PÉRDIDA EN TIENDAS PUNTUALES: "
      f"{len(focos)} combinaciones\n{'=' * 95}")
if len(focos):
    d = focos.sort_values().head(15).reset_index()
    d["producto"] = d.sku_erp.map(productos.set_index("sku_erp")["nombre"]).str[:30]
    d["ciudad"] = d.tienda_id.map(tiendas.ciudad)
    print(d[["producto", "tienda_id", "ciudad", "margen"]].round(1).to_string(index=False))
    