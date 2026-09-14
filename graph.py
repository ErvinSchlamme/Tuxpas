"""
Ventas mensuales y crecimiento MoM por canal: físico vs e-commerce.

Decisiones documentadas:
  - E-commerce convertido a MXN con el tipo de cambio del día de la orden.
    Sin esto los canales no son comparables (USD, EUR y MXN mezclados).
  - Físico: comprobante E = devolución; T, P, N no son ventas.
  - Las devoluciones no tienen referencia a la venta original, por lo que
    no se pueden atribuir al canal de compra. Shopify no reporta ninguna
    devolución: los refunds viven en un feed aparte (pregunta al cliente).
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

RAW = Path(r"C:\Users\ervin\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm\LocalState\sessions\ADF274A702466B968037426F7D476870AC57C49F\transfers\2026-37\datos_cafenorte (2)\datos")

TIPOS_VENTA = {"I": 1, "E": -1}
TIPOS_NO_VENTA = {"T", "P", "N"}

pd.set_option("display.width", 200)


# ---------------------------------------------------------------- carga
fx = pd.read_csv(RAW / "exchange_rates.csv", parse_dates=["fecha"])
ventas = pd.read_csv(RAW / "sales.csv", parse_dates=["fecha_hora"])
ecom = pd.read_parquet(RAW / "ecommerce_orders.parquet")
ecom["fecha"] = pd.to_datetime(ecom["fecha"])


# ------------------------------------------------------- canal físico
desconocidos = set(ventas.tipo_comprobante) - set(TIPOS_VENTA) - TIPOS_NO_VENTA
if desconocidos:
    raise SystemExit(f"Tipos no contemplados: {sorted(desconocidos)}")

v = ventas[ventas.tipo_comprobante.isin(TIPOS_VENTA)].copy()
v["mes"] = v.fecha_hora.dt.to_period("M")
v["es_devolucion"] = v.tipo_comprobante == "E"


# ---------------------------------------------------- canal e-commerce
e = ecom.copy()
e["dia"] = e.fecha.dt.normalize()

filas = len(e)
e = e.merge(fx, left_on=["dia", "currency"], right_on=["fecha", "currency"],
            how="left", suffixes=("", "_fx"))
assert len(e) == filas, "El merge con tipos de cambio multiplicó filas"

e.loc[e.currency == "MXN", "rate_to_mxn"] = 1.0
sin_tasa = e.rate_to_mxn.isna()
if sin_tasa.any():
    print(f"(!) {sin_tasa.sum():,} órdenes sin tipo de cambio del día "
          "-> se usa la tasa vigente más cercana")
    e = e.sort_values("dia")
    e["rate_to_mxn"] = e.groupby("currency")["rate_to_mxn"].ffill().bfill()

e["es_devolucion"] = (e.amount < 0) | (e.cantidad < 0)
e["mxn"] = e.amount * e.rate_to_mxn
e["mes"] = e.fecha.dt.to_period("M")

print(f"Devoluciones detectadas -> físico: {v.es_devolucion.sum():,} | "
      f"e-commerce: {e.es_devolucion.sum():,}")
if not e.es_devolucion.any():
    print("    El e-commerce no registra devoluciones: Shopify las exporta en un\n"
          "    feed de refunds aparte. Pregunta abierta para el cliente.")


# ---------------------------------------------------- series mensuales
df = pd.DataFrame({
    "fisico": v[~v.es_devolucion].groupby("mes").monto.sum(),
    "ecommerce": e[~e.es_devolucion].groupby("mes").mxn.sum(),
    "fisico_dev": v[v.es_devolucion].groupby("mes").monto.sum(),
}).fillna(0).sort_index()

df["fisico_mom"] = df.fisico.pct_change() * 100
df["ecom_mom"] = df.ecommerce.replace(0, pd.NA).pct_change() * 100
df["tasa_dev_fisico"] = df.fisico_dev / df.fisico * 100

print(f"\n{'=' * 80}\nVENTAS MENSUALES (MXN) Y CRECIMIENTO MoM\n{'=' * 80}")
print(df[["fisico", "ecommerce", "fisico_mom", "ecom_mom"]].round(1).to_string())

total_f, total_e = df.fisico.sum(), df.ecommerce.sum()
print(f"\nTotal físico    : {total_f:>15,.0f} MXN ({total_f / (total_f + total_e):.1%})")
print(f"Total e-commerce: {total_e:>15,.0f} MXN ({total_e / (total_f + total_e):.1%})")
print(f"Devoluciones    : {df.fisico_dev.sum():,.0f} MXN "
      f"({df.fisico_dev.sum() / total_f:.1%} de las ventas físicas)")
print(f"\nMoM promedio -> físico: {df.fisico_mom.mean():+.1f}% | "
      f"e-commerce: {df.ecom_mom.mean():+.1f}%")


# ------------------------------------------------------------ gráficas
x = df.index.astype(str)
pos = range(len(x))
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8))

ax1.plot(x, df.fisico / 1e6, "o-", lw=2, color="#2E5E4E", label="Físico")
ax1.plot(x, df.ecommerce / 1e6, "s-", lw=2, color="#C87941", label="E-commerce")
ax1.fill_between(x, df.fisico / 1e6, alpha=.15, color="#2E5E4E")
ax1.fill_between(x, df.ecommerce / 1e6, alpha=.15, color="#C87941")
ax1.set_ylabel("Millones MXN")
ax1.set_title("CaféNorte · Ventas mensuales por canal", fontsize=13, weight="bold")
ax1.legend()
ax1.grid(alpha=.3)
ax1.tick_params(labelrotation=45)

ancho = .4
ax2.bar([p - ancho / 2 for p in pos], df.fisico_mom, ancho,
        color="#2E5E4E", label="Físico")
ax2.bar([p + ancho / 2 for p in pos], df.ecom_mom, ancho,
        color="#C87941", label="E-commerce")
ax2.axhline(0, color="black", lw=.8)
ax2.set_ylabel("Crecimiento MoM (%)")
ax2.set_title("Crecimiento mes contra mes", fontsize=13, weight="bold")
ax2.set_xticks(list(pos))
ax2.set_xticklabels(x, rotation=45, ha="right")
ax2.legend()
ax2.grid(alpha=.3, axis="y")

plt.tight_layout()
plt.savefig("ventas_por_canal.png", dpi=150)
print("\nGráfica guardada: ventas_por_canal.png")
plt.show()