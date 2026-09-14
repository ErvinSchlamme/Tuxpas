"""
Pregunta 1: Top 10 SKUs por rotación de inventario.

Rotación anualizada = (unidades vendidas netas / inventario promedio)
                      escalado a 365 días.

Decisiones documentadas:
  - Rotación en unidades, no en valor. El costo varía en el tiempo
    (cost_history) y la pregunta se responde igual con unidades.
  - Solo tienda física: el e-commerce no tiene tienda_id, sus unidades no
    son atribuibles al inventario de los snapshots.
  - Comprobantes T (traslado), P (pago) y N (nómina) no son ventas.
    E (nota de crédito) resta unidades.
  - Stock no numérico = dato faltante, nunca cero.
  - La ventana efectiva la fijan los snapshots (182 días), no las ventas.
    La anualización asume ausencia de estacionalidad; la ventana incluye
    temporada navideña, por lo que probablemente sobreestima el año.
"""

import json
from pathlib import Path

import pandas as pd

RAW = Path(r"C:\Users\ervin\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\LocalState\sessions\ADF274A702466B968037426F7D476870AC57C49F\transfers\2026-37\datos_cafenorte (2)\datos")

TIPOS_VENTA = {"I": 1, "E": -1}
TIPOS_NO_VENTA = {"T", "P", "N"}

pd.set_option("display.width", 200)


# ---------------------------------------------------------------- carga
ventas = pd.read_csv(RAW / "sales.csv", parse_dates=["fecha_hora"])

with open(RAW / "inventory.json", encoding="utf-8") as f:
    erp = json.load(f)

mapeo = pd.DataFrame(erp["sku_mappings"])[["sku_pos", "sku_erp"]]
nombres = pd.DataFrame(erp["catalogo"]["productos"]).set_index("sku_erp")["nombre"]

snaps = pd.DataFrame(erp["snapshots"])
snaps["fecha"] = pd.to_datetime(snaps["fecha"])
snaps["cantidad_en_stock"] = pd.to_numeric(snaps["cantidad_en_stock"],
                                           errors="coerce")


# ------------------------------------------------------- validaciones
print("VALIDACIONES")
print("-" * 60)

desconocidos = set(ventas.tipo_comprobante) - set(TIPOS_VENTA) - TIPOS_NO_VENTA
if desconocidos:
    raise SystemExit(f"Tipos de comprobante no contemplados: {sorted(desconocidos)}")

if mapeo.sku_pos.duplicated().any():
    raise SystemExit("sku_pos duplicado: el merge multiplicaría filas de venta.")

sin_stock = snaps.cantidad_en_stock.isna().mean()
print(f"Snapshots con stock no numérico : {sin_stock:.1%} (tratados como faltantes)")


# ---------------------------------------------- ventas netas en ventana
inicio, fin = snaps.fecha.min(), snaps.fecha.max()
dias = (fin - inicio).days + 1

ventas = ventas[ventas.tipo_comprobante.isin(TIPOS_VENTA)]
ventas = ventas[ventas.fecha_hora.between(inicio, fin + pd.Timedelta(days=1))].copy()
ventas["unidades"] = ventas.cantidad * ventas.tipo_comprobante.map(TIPOS_VENTA)

filas = len(ventas)
ventas = ventas.merge(mapeo, left_on="sku", right_on="sku_pos", how="left")
assert len(ventas) == filas, "El merge alteró el número de filas de venta"

sin_mapeo = ventas.sku_erp.isna()
print(f"Ventas sin equivalente en ERP   : {ventas.loc[sin_mapeo, 'unidades'].sum() / ventas.unidades.sum():.1%} "
      f"de las unidades ({sin_mapeo.sum():,} filas, {ventas.loc[sin_mapeo, 'sku'].nunique()} SKUs) -> excluidas")

vendidas = (ventas.dropna(subset=["sku_erp"])
                  .groupby("sku_erp")["unidades"].sum())


# ---------------------------------------------- inventario promedio
# Se suma el stock de las 40 tiendas por día y luego se promedia sobre los
# días: promediar las filas directamente daría el stock medio POR TIENDA.
# min_count=1 evita que un día sin ningún dato cuente como inventario cero.
inventario = (snaps.groupby(["fecha", "sku_erp"])["cantidad_en_stock"]
                   .sum(min_count=1)
                   .groupby("sku_erp").mean())


# ------------------------------------------------------------ resultado
rot = pd.DataFrame({
    "unidades_vendidas": vendidas,
    "inventario_promedio": inventario,
}).query("inventario_promedio > 0")

rot.insert(0, "producto", rot.index.map(nombres))
rot["rotacion_anual"] = (rot.unidades_vendidas / rot.inventario_promedio) * (365 / dias)

print(f"\nVentana: {inicio.date()} a {fin.date()} ({dias} días) | "
      f"{len(rot)} SKUs evaluados | mediana {rot.rotacion_anual.median():.1f}x")

print(f"\n{'=' * 60}\nTOP 10 SKUs POR ROTACIÓN ANUAL\n{'=' * 60}")
print(rot.nlargest(10, "rotacion_anual").round(2).to_string())