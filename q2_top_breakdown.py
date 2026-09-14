"""
Pregunta 2: Tiendas con quiebres de stock en el último trimestre.

Quiebre = racha de >= 3 días consecutivos con stock 0 para una combinación
tienda-SKU. Detección por gaps-and-islands sobre el calendario real:
los días sin registro rompen la racha y el stock no numérico (NaN) no
cuenta como cero.
"""

import json
from pathlib import Path

import pandas as pd

RAW = Path(r"C:\Users\ervin\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\LocalState\sessions\ADF274A702466B968037426F7D476870AC57C49F\transfers\2026-37\datos_cafenorte (2)\datos")

DIAS_TRIMESTRE = 90
MIN_DIAS = 3

pd.set_option("display.width", 200)

with open(RAW / "inventory.json", encoding="utf-8") as f:
    erp = json.load(f)

tiendas = pd.DataFrame(erp["tiendas_info"]).set_index("tienda_id")
nombres = pd.DataFrame(erp["catalogo"]["productos"]).set_index("sku_erp")["nombre"]

snaps = pd.DataFrame(erp["snapshots"])
snaps["fecha"] = pd.to_datetime(snaps["fecha"])
snaps["cantidad_en_stock"] = pd.to_numeric(snaps["cantidad_en_stock"], errors="coerce")

fin = snaps.fecha.max()
inicio = fin - pd.Timedelta(days=DIAS_TRIMESTRE - 1)
snaps = snaps[snaps.fecha.between(inicio, fin)].sort_values(
    ["tienda_id", "sku_erp", "fecha"])

if snaps.duplicated(subset=["fecha", "tienda_id", "sku_erp"]).any():
    raise SystemExit("Snapshots duplicados por (fecha, tienda, sku).")

# Gaps and islands: dentro de una racha de ceros, la diferencia entre el
# orden global y el orden entre ceros se mantiene constante.
llave = ["tienda_id", "sku_erp"]
en_cero = snaps.cantidad_en_stock == 0          # NaN -> False
snaps["grupo"] = (snaps.groupby(llave, sort=False).cumcount()
                  - snaps[en_cero].groupby(llave, sort=False).cumcount())

rachas = (snaps[en_cero].groupby(llave + ["grupo"])
          .agg(dias=("fecha", "size"), desde=("fecha", "min"), hasta=("fecha", "max"))
          .reset_index())

# Descarta rachas con huecos de calendario (días sin registro entre ceros).
continua = (rachas.hasta - rachas.desde).dt.days + 1 == rachas.dias
quiebres = rachas[(rachas.dias >= MIN_DIAS) & continua]

print(f"Ventana: {inicio.date()} a {fin.date()} | "
      f"{len(quiebres):,} quiebres de >= {MIN_DIAS} días en "
      f"{quiebres.tienda_id.nunique()} de {snaps.tienda_id.nunique()} tiendas")

por_tienda = (quiebres.groupby("tienda_id")
              .agg(quiebres=("dias", "size"), dias_totales=("dias", "sum"),
                   dias_max=("dias", "max"), skus=("sku_erp", "nunique"))
              .join(tiendas[["ciudad", "region"]])
              .sort_values("quiebres", ascending=False))

print(f"\n{'=' * 70}\nTIENDAS AFECTADAS\n{'=' * 70}")
print(por_tienda.to_string())

print(f"\n{'=' * 70}\nDESGLOSE POR PRODUCTO (top 5 tiendas)\n{'=' * 70}")
for t in por_tienda.head(5).index:
    d = quiebres[quiebres.tienda_id == t]
    print(f"\n{t} - {tiendas.loc[t, 'ciudad']} | {len(d)} quiebres, {d.dias.sum()} días")
    detalle = (d.groupby("sku_erp")
               .agg(veces=("dias", "size"), dias=("dias", "sum"), max=("dias", "max"))
               .sort_values("dias", ascending=False))
    detalle.insert(0, "producto", detalle.index.map(nombres).str[:32])
    print(detalle.head(8).to_string())