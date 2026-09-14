"""
Pipeline analitico CafeNorte â€” respuestas a las 4 preguntas de negocio.

Supuestos principales:
  - CFDI: I=venta, E=nota de credito (resta), T/P/N no son operaciones de venta.
  - Stock no numerico = dato faltante, nunca cero.
  - E-commerce convertido a MXN con el tipo de cambio del dia de la orden.
  - Costo vigente al momento de cada venta (no el costo actual).
  - P1 y P4 solo cubren canal fisico: el e-commerce no tiene tienda_id.
  - P1: la ventana la fijan los snapshots (182 dias), no las ventas.
    La anualizacion asume ausencia de estacionalidad.

Uso: python final.py
"""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

BASE = Path(__file__).parent
RAW = BASE / "data" / "raw"
OUT = BASE / "outputs"

TIPOS_VENTA = {"I": 1, "E": -1}
TIPOS_NO_VENTA = {"T", "P", "N"}
DIAS_TRIMESTRE = 90
MIN_DIAS_QUIEBRE = 3

pd.set_option("display.width", 220)


def titulo(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ ingesta
def cargar():
    with open(RAW / "inventory.json", encoding="utf-8") as f:
        erp = json.load(f)

    productos = pd.DataFrame(erp["catalogo"]["productos"])

    snaps = pd.DataFrame(erp["snapshots"])
    snaps["fecha"] = pd.to_datetime(snaps["fecha"])
    snaps["cantidad_en_stock"] = pd.to_numeric(snaps["cantidad_en_stock"],
                                               errors="coerce")

    base = (productos[["sku_erp", "cost_history"]]
            .explode("cost_history")
            .reset_index(drop=True))
    costos = pd.concat([base[["sku_erp"]],
                        pd.json_normalize(base.cost_history)], axis=1)
    costos["fecha_vigencia"] = pd.to_datetime(costos["fecha_vigencia"])

    ecom = pd.read_parquet(RAW / "ecommerce_orders.parquet")
    ecom["fecha"] = pd.to_datetime(ecom["fecha"])

    mapeo = pd.DataFrame(erp["sku_mappings"])

    return {
        "pos": pd.read_csv(RAW / "sales.csv", parse_dates=["fecha_hora"]),
        "ecom": ecom,
        "fx": pd.read_csv(RAW / "exchange_rates.csv", parse_dates=["fecha"]),
        "tiendas": pd.DataFrame(erp["tiendas_info"]).set_index("tienda_id"),
        "mapeo": mapeo[["sku_pos", "sku_erp"]],
        "mapeo_full": mapeo,
        "productos": productos,
        "nombres": productos.set_index("sku_erp")["nombre"],
        "snapshots": snaps,
        "costos": costos.sort_values("fecha_vigencia"),
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ normalizacion
def normalizar_pos(pos, mapeo, costos):
    desconocidos = set(pos.tipo_comprobante) - set(TIPOS_VENTA) - TIPOS_NO_VENTA
    if desconocidos:
        raise ValueError(f"Tipos de comprobante no contemplados: {sorted(desconocidos)}")
    if mapeo.sku_pos.duplicated().any():
        raise ValueError("sku_pos duplicado: el merge multiplicaria ventas.")

    v = pos[pos.tipo_comprobante.isin(TIPOS_VENTA)].copy()
    signo = v.tipo_comprobante.map(TIPOS_VENTA)
    v["unidades"] = v.cantidad * signo
    v["ingreso"] = v.monto * signo
    v["es_devolucion"] = v.tipo_comprobante == "E"
    v["mes"] = v.fecha_hora.dt.to_period("M")

    filas = len(v)
    v = v.merge(mapeo, left_on="sku", right_on="sku_pos", how="left")
    if len(v) != filas:
        raise ValueError("El merge con el mapeo altero el numero de ventas.")

    # Costo vigente por SKU. merge_asof busca hacia atras la ultima vigencia;
    # usar el costo actual generaria perdidas retroactivas que nunca ocurrieron.
    v = v.sort_values("fecha_hora")
    sub = v[v.sku_erp.notna()].reset_index()
    sub = pd.merge_asof(sub, costos[["sku_erp", "fecha_vigencia", "costo_mxn"]],
                        left_on="fecha_hora", right_on="fecha_vigencia",
                        by="sku_erp", direction="backward")
    v["costo_mxn"] = pd.Series(sub.costo_mxn.values, index=sub["index"])

    v["costo_total"] = v.unidades * v.costo_mxn
    v["margen"] = v.ingreso - v.costo_total
    return v


def normalizar_ecom(ecom, fx):
    e = ecom.copy()
    e["dia"] = e.fecha.dt.normalize()

    filas = len(e)
    e = e.merge(fx, left_on=["dia", "currency"], right_on=["fecha", "currency"],
                how="left", suffixes=("", "_fx"))
    if len(e) != filas:
        raise ValueError("El merge con tipos de cambio duplico ordenes.")

    e.loc[e.currency == "MXN", "rate_to_mxn"] = 1.0
    if e.rate_to_mxn.isna().any():
        e = e.sort_values("dia")
        e["rate_to_mxn"] = e.groupby("currency")["rate_to_mxn"].ffill().bfill()

    e["ingreso"] = e.amount * e.rate_to_mxn
    e["mes"] = e.fecha.dt.to_period("M")
    return e


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ calidad de datos
class Chequeo:
    """Resultado de una verificacion: OK, AVISO (se sigue) o ERROR (se aborta)."""

    def __init__(self, nombre, estado, detalle, impacto=""):
        self.nombre, self.estado = nombre, estado
        self.detalle, self.impacto = detalle, impacto

    def __str__(self):
        marca = {"OK": "[ OK ]", "AVISO": "[AVISO]", "ERROR": "[ERROR]"}[self.estado]
        linea = f"{marca} {self.nombre:<34} {self.detalle}"
        return linea + (f"\n{'':>8}-> {self.impacto}" if self.impacto else "")


def verificar_calidad(d, ventas, ecom):
    """
    Corre antes de publicar resultados. ERROR aborta el pipeline; AVISO
    documenta una limitacion conocida que acota la lectura de una respuesta.
    """
    ch = []
    pos, snaps = d["pos"], d["snapshots"]

    # 1. Reconciliacion: el ingreso del modelo debe reproducir la fuente cruda.
    esperado = (pos.loc[pos.tipo_comprobante == "I", "monto"].sum()
                - pos.loc[pos.tipo_comprobante == "E", "monto"].sum())
    dif = abs(ventas.ingreso.sum() - esperado)
    ch.append(Chequeo(
        "Reconciliacion vs fuente",
        "OK" if dif < 0.01 else "ERROR",
        f"modelo {ventas.ingreso.sum():,.0f} vs origen {esperado:,.0f} MXN",
        "" if dif < 0.01 else "El modelo no reproduce la fuente: no publicar."))

    # 2. Trazabilidad de filas entre etapas.
    esperadas = pos.tipo_comprobante.isin(TIPOS_VENTA).sum()
    ch.append(Chequeo(
        "Trazabilidad de filas",
        "OK" if len(ventas) == esperadas else "ERROR",
        f"{len(pos):,} leidas -> {len(pos) - esperadas:,} no-venta -> "
        f"{len(ventas):,} en modelo",
        "" if len(ventas) == esperadas else "El join altero el conteo de ventas."))

    # 3. Unicidad de llaves primarias.
    dups = (pos.venta_id.duplicated().sum()
            + d["ecom"].order_id.duplicated().sum()
            + snaps.duplicated(subset=["fecha", "tienda_id", "sku_erp"]).sum())
    ch.append(Chequeo(
        "Unicidad de llaves",
        "OK" if dups == 0 else "ERROR",
        f"{dups} duplicados en venta_id / order_id / (fecha,tienda,sku)"))

    # 4. Cobertura del mapeo POS -> ERP. Afecta P1 y P4.
    sin_mapeo = ventas.sku_erp.isna()
    pct = sin_mapeo.mean()
    ch.append(Chequeo(
        "Cobertura mapeo POS->ERP",
        "OK" if pct < 0.05 else "AVISO",
        f"{pct:.1%} de ventas sin sku_erp ({ventas.loc[sin_mapeo, 'sku'].nunique()} SKUs)",
        "" if pct < 0.05 else "P1 y P4 excluyen estas ventas: sin sku_erp no hay "
                             "inventario ni costo. Consultar al cliente."))

    # 5. Integridad numerica del inventario. Afecta P1 y P2.
    nan = snaps.cantidad_en_stock.isna().mean()
    neg = (snaps.cantidad_en_stock < 0).sum()
    ch.append(Chequeo(
        "Integridad de snapshots",
        "OK" if nan < 0.01 and neg == 0 else "AVISO",
        f"{nan:.1%} no numerico, {neg:,} negativos",
        "" if nan < 0.01 and neg == 0
        else "Faltantes tratados como dato ausente, no como stock cero."))

    # 6. Densidad de la malla tienda x SKU x dia. Afecta P2.
    esperada = (snaps.fecha.nunique() * snaps.tienda_id.nunique()
                * snaps.sku_erp.nunique())
    densidad = len(snaps) / esperada
    ch.append(Chequeo(
        "Densidad del inventario",
        "OK" if densidad > 0.95 else "AVISO",
        f"{densidad:.1%} de la malla completa ({len(snaps):,} de {esperada:,})",
        "" if densidad > 0.95
        else "No toda combinacion tienda-SKU se inventaria a diario."))

    # 7. Continuidad del calendario. Afecta P2: un hueco parte una racha.
    dias_esp = (snaps.fecha.max() - snaps.fecha.min()).days + 1
    faltan = dias_esp - snaps.fecha.nunique()
    ch.append(Chequeo(
        "Continuidad del calendario",
        "OK" if faltan == 0 else "AVISO",
        f"{faltan} dias sin snapshot de {dias_esp}",
        "" if faltan == 0
        else "Las rachas con hueco de calendario se descartan en P2."))

    # 8. Contradiccion POS vs ERP: ventas en dias marcados con stock cero.
    cero = snaps[snaps.cantidad_en_stock == 0][["fecha", "tienda_id", "sku_erp"]]
    v = ventas[~ventas.es_devolucion & ventas.sku_erp.notna()].copy()
    v["fecha"] = v.fecha_hora.dt.normalize()
    conflicto = v.merge(cero, on=["fecha", "tienda_id", "sku_erp"], how="inner")
    pct_c = len(conflicto) / len(v) if len(v) else 0
    ch.append(Chequeo(
        "Coherencia POS vs ERP",
        "OK" if pct_c < 0.01 else "AVISO",
        f"{len(conflicto):,} ventas ({pct_c:.1%}) en dias con stock 0 registrado",
        "" if pct_c < 0.01
        else "Los dos sistemas se contradicen: P2 puede sobrestimar quiebres."))

    # 9. Cobertura de costos. Afecta P4.
    sin_costo = ventas[ventas.sku_erp.notna()].costo_mxn.isna().mean()
    ch.append(Chequeo(
        "Cobertura de costos",
        "OK" if sin_costo < 0.05 else "AVISO",
        f"{sin_costo:.1%} de ventas mapeadas sin costo vigente",
        "" if sin_costo < 0.05
        else "Ventas previas a la primera vigencia; P4 las excluye."))

    # 10. Plausibilidad de los tipos de cambio. Afecta P3.
    usd = d["fx"][d["fx"].currency == "USD"].rate_to_mxn
    ok_fx = usd.between(14, 26).all()
    ch.append(Chequeo(
        "Tipos de cambio plausibles",
        "OK" if ok_fx else "ERROR",
        f"USD/MXN entre {usd.min():.2f} y {usd.max():.2f}",
        "" if ok_fx else "Rango implausible: revisar la fuente de FX."))

    # 11. Refunds de Shopify. Afecta P3.
    refunds = (ecom.amount < 0).sum()
    ch.append(Chequeo(
        "Devoluciones e-commerce",
        "OK" if refunds > 0 else "AVISO",
        f"{refunds} refunds en {len(ecom):,} ordenes",
        "" if refunds > 0
        else "Shopify exporta refunds en un feed aparte: P3 sobrestima el "
             "ingreso neto del canal digital."))

    return ch


def reportar_calidad(chequeos):
    titulo("CALIDAD DE DATOS")
    for c in chequeos:
        print(c)

    errores = [c for c in chequeos if c.estado == "ERROR"]
    avisos = [c for c in chequeos if c.estado == "AVISO"]
    print(f"\n{len(chequeos) - len(errores) - len(avisos)} OK · "
          f"{len(avisos)} avisos · {len(errores)} errores")

    if errores:
        raise SystemExit("\nPipeline detenido: hay chequeos en ERROR. "
                         "Los resultados no se publican.")
    if avisos:
        print("\nLos avisos son limitaciones conocidas de las fuentes, no fallas\n"
              "del pipeline. Cada uno acota la lectura de la respuesta indicada.")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ P1 · rotacion
def p1_rotacion(ventas, snaps, nombres):
    inicio, fin = snaps.fecha.min(), snaps.fecha.max()
    dias = (fin - inicio).days + 1

    v = ventas[ventas.fecha_hora.between(inicio, fin + pd.Timedelta(days=1))]
    vendidas = v.dropna(subset=["sku_erp"]).groupby("sku_erp").unidades.sum()

    # Stock sumado sobre las 40 tiendas por dia, luego promediado sobre los
    # dias. min_count=1 evita que un dia sin datos cuente como inventario 0.
    inventario = (snaps.groupby(["fecha", "sku_erp"]).cantidad_en_stock
                  .sum(min_count=1).groupby("sku_erp").mean())

    rot = pd.DataFrame({"unidades_vendidas": vendidas,
                        "inventario_promedio": inventario}
                       ).query("inventario_promedio > 0")
    rot.insert(0, "producto", rot.index.map(nombres))
    rot["rotacion_anual"] = (rot.unidades_vendidas / rot.inventario_promedio
                             ) * (365 / dias)
    return rot.sort_values("rotacion_anual", ascending=False), inicio, fin, dias


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ P2 · quiebres
def p2_quiebres(snaps):
    fin = snaps.fecha.max()
    inicio = fin - pd.Timedelta(days=DIAS_TRIMESTRE - 1)
    s = snaps[snaps.fecha.between(inicio, fin)].sort_values(
        ["tienda_id", "sku_erp", "fecha"])

    if s.duplicated(subset=["fecha", "tienda_id", "sku_erp"]).any():
        raise ValueError("Snapshots duplicados por (fecha, tienda, sku).")

    # Gaps and islands: dentro de una racha de ceros, la diferencia entre el
    # orden global y el orden entre ceros permanece constante.
    llave = ["tienda_id", "sku_erp"]
    en_cero = s.cantidad_en_stock == 0          # NaN -> False
    s = s.assign(grupo=s.groupby(llave, sort=False).cumcount()
                 - s[en_cero].groupby(llave, sort=False).cumcount())

    rachas = (s[en_cero].groupby(llave + ["grupo"])
              .agg(dias=("fecha", "size"), desde=("fecha", "min"),
                   hasta=("fecha", "max")).reset_index())
    continua = (rachas.hasta - rachas.desde).dt.days + 1 == rachas.dias
    return rachas[(rachas.dias >= MIN_DIAS_QUIEBRE) & continua], inicio, fin


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ P3 · MoM
def p3_mom(ventas, ecom):
    df = pd.DataFrame({
        "fisico": ventas[~ventas.es_devolucion].groupby("mes").monto.sum(),
        "ecommerce": ecom.groupby("mes").ingreso.sum(),
        "devoluciones": ventas[ventas.es_devolucion].groupby("mes").monto.sum(),
    }).fillna(0).sort_index()

    df["fisico_mom"] = df.fisico.pct_change() * 100
    # replace(0) evita un MoM infinito cuando el canal arranca desde cero.
    df["ecom_mom"] = df.ecommerce.replace(0, pd.NA).pct_change() * 100
    return df


def graficar(mom):
    x, pos = mom.index.astype(str), range(len(mom))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8))

    ax1.plot(x, mom.fisico / 1e6, "o-", lw=2, color="#2E5E4E", label="Fisico")
    ax1.plot(x, mom.ecommerce / 1e6, "s-", lw=2, color="#C87941", label="E-commerce")
    ax1.fill_between(x, mom.fisico / 1e6, alpha=.15, color="#2E5E4E")
    ax1.fill_between(x, mom.ecommerce / 1e6, alpha=.15, color="#C87941")
    ax1.set_ylabel("Millones MXN")
    ax1.set_title("CafeNorte · Ventas mensuales por canal", fontsize=13, weight="bold")
    ax1.legend()
    ax1.grid(alpha=.3)
    ax1.tick_params(labelrotation=45)

    a = .4
    ax2.bar([p - a / 2 for p in pos], mom.fisico_mom, a, color="#2E5E4E", label="Fisico")
    ax2.bar([p + a / 2 for p in pos], mom.ecom_mom, a, color="#C87941", label="E-commerce")
    ax2.axhline(0, color="black", lw=.8)
    ax2.set_ylabel("Crecimiento MoM (%)")
    ax2.set_title("Crecimiento mes contra mes", fontsize=13, weight="bold")
    ax2.set_xticks(list(pos))
    ax2.set_xticklabels(x, rotation=45, ha="right")
    ax2.legend()
    ax2.grid(alpha=.3, axis="y")

    plt.tight_layout()
    plt.savefig(OUT / "ventas_por_canal.png", dpi=150)
    plt.show()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ P4 · margen
def p4_margen(ventas, productos):
    v = ventas.dropna(subset=["sku_erp", "costo_mxn"])

    por_producto = (v.groupby("sku_erp")
                    .agg(unidades=("unidades", "sum"), ingreso=("ingreso", "sum"),
                         costo=("costo_total", "sum"), margen=("margen", "sum"),
                         tiendas=("tienda_id", "nunique"))
                    .join(productos.set_index("sku_erp")[["nombre", "categoria"]]))
    por_producto["margen_pct"] = por_producto.margen / por_producto.ingreso * 100

    negativos = por_producto[por_producto.margen < 0].sort_values("margen")
    detalle = (v[v.sku_erp.isin(negativos.index)]
               .groupby(["sku_erp", "tienda_id"])
               .agg(unidades=("unidades", "sum"), ingreso=("ingreso", "sum"),
                    margen=("margen", "sum")))
    return negativos, detalle.sort_values("margen")

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ modelo analitico (DuckDB)
def persistir(ventas, ecom, d):
    """
    Persiste un modelo dimensional consultable en SQL.

    Conciliacion de canales: ambos se unifican en fact_ventas con llave
    comun sku_erp. El e-commerce llega por product_handle -> sku_erp via
    sku_mappings y queda con tienda_id nulo (no atribuible a tienda) y sin
    costo (el catalogo de costos es del ERP, que solo cubre tienda fisica).
    """
    import duckdb

    fisico = (ventas.assign(canal="fisico", id=ventas.venta_id,
                            fecha=ventas.fecha_hora)
              [["id", "canal", "fecha", "tienda_id", "sku_erp", "unidades",
                "ingreso", "costo_total", "margen", "es_devolucion"]])

    handle_a_sku = (d["mapeo_full"].dropna(subset=["handle"])
                    .set_index("handle")["sku_erp"])
    digital = (ecom.assign(canal="ecommerce", id=ecom.order_id,
                           tienda_id=pd.NA,
                           sku_erp=ecom.product_handle.map(handle_a_sku),
                           unidades=ecom.cantidad,
                           costo_total=pd.NA, margen=pd.NA,
                           es_devolucion=False)
               [["id", "canal", "fecha", "tienda_id", "sku_erp", "unidades",
                 "ingreso", "costo_total", "margen", "es_devolucion"]])

    tablas = {
        "fact_ventas": pd.concat([fisico, digital], ignore_index=True),
        "fact_inventario": d["snapshots"],
        "dim_tienda": d["tiendas"].reset_index(),
        "dim_producto": d["productos"][["sku_erp", "nombre", "categoria"]],
        "dim_costo": d["costos"],
    }

    ruta = OUT / "cafenorte.duckdb"
    ruta.unlink(missing_ok=True)
    con = duckdb.connect(str(ruta))
    for nombre, tabla in tablas.items():
        con.register("tmp", tabla)
        con.execute(f"CREATE TABLE {nombre} AS SELECT * FROM tmp")
        print(f"  {nombre:<18} {len(tabla):>8,} filas")
    con.close()

    sin_sku = digital.sku_erp.isna().mean()
    print(f"\n  I“rdenes de e-commerce sin sku_erp: {sin_sku:.1%} "
          "(handles fuera del catalogo del ERP)")
    return ruta

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€  main
def main():
    OUT.mkdir(exist_ok=True)
    d = cargar()

    ventas = normalizar_pos(d["pos"], d["mapeo"], d["costos"])
    ecom = normalizar_ecom(d["ecom"], d["fx"])

    reportar_calidad(verificar_calidad(d, ventas, ecom))

    # â”€â”€ P1
    rot, ini1, fin1, dias1 = p1_rotacion(ventas, d["snapshots"], d["nombres"])
    titulo(f"P1 · TOP 10 SKUs POR ROTACII“N ({ini1.date()} a {fin1.date()}, {dias1} dias)")
    print(rot.head(10).round(2).to_string())
    print(f"\n{len(rot)} SKUs evaluados | mediana {rot.rotacion_anual.median():.1f}x")

    # â”€â”€ P2
    quiebres, ini2, fin2 = p2_quiebres(d["snapshots"])
    por_tienda = (quiebres.groupby("tienda_id")
                  .agg(quiebres=("dias", "size"), dias_totales=("dias", "sum"),
                       dias_max=("dias", "max"), skus=("sku_erp", "nunique"))
                  .join(d["tiendas"][["ciudad", "region"]])
                  .sort_values("quiebres", ascending=False))
    titulo(f"P2 · QUIEBRES DE >= {MIN_DIAS_QUIEBRE} DIAS ({ini2.date()} a {fin2.date()})")
    print(f"{len(quiebres):,} quiebres en {quiebres.tienda_id.nunique()} tiendas\n")
    print(por_tienda.head(10).to_string())

    print("\nDesglose por producto (top 3 tiendas):")
    for t in por_tienda.head(3).index:
        det = (quiebres[quiebres.tienda_id == t].groupby("sku_erp")
               .agg(veces=("dias", "size"), dias=("dias", "sum"), max=("dias", "max"))
               .sort_values("dias", ascending=False))
        det.insert(0, "producto", det.index.map(d["nombres"]).str[:32])
        print(f"\n{t} · {d['tiendas'].loc[t, 'ciudad']}")
        print(det.head(6).to_string())

    # â”€â”€ P3
    mom = p3_mom(ventas, ecom)
    titulo("P3 · VENTAS MENSUALES POR CANAL (MXN) Y CRECIMIENTO MoM")
    print(mom[["fisico", "ecommerce", "fisico_mom", "ecom_mom"]].round(1).to_string())
    tf, te = mom.fisico.sum(), mom.ecommerce.sum()
    print(f"\nFisico {tf:,.0f} MXN ({tf / (tf + te):.1%}) | "
          f"E-commerce {te:,.0f} MXN ({te / (tf + te):.1%})")
    print(f"MoM promedio -> fisico {mom.fisico_mom.mean():+.1f}% | "
          f"e-commerce {mom.ecom_mom.mean():+.1f}%")

    # â”€â”€ P4
    negativos, detalle = p4_margen(ventas, d["productos"])
    titulo(f"P4 · PRODUCTOS CON MARGEN NEGATIVO ({len(negativos)})")
    if negativos.empty:
        print("Ningun producto tiene margen negativo agregado.")
    else:
        print(negativos.round(1).to_string())
        print(f"\nPerdida total: {negativos.margen.sum():,.0f} MXN")
        for sku in negativos.index:
            det = detalle.loc[sku].join(d["tiendas"][["ciudad", "region"]])
            det["margen_pct"] = det.margen / det.ingreso * 100
            print(f"\n{negativos.loc[sku, 'nombre']} ({sku}) · "
                  f"{len(det)} tiendas afectadas")
            print(det.head(10).round(1).to_string())

    titulo("MODELO ANALITICO PERSISTIDO")
    persistir(ventas, ecom, d)

    for nombre, tabla in [("p1_rotacion", rot), ("p2_quiebres", quiebres),
                          ("p2_por_tienda", por_tienda), ("p3_mom", mom),
                          ("p4_margen_negativo", negativos),
                          ("p4_detalle_tienda", detalle)]:
        tabla.to_csv(OUT / f"{nombre}.csv")

    print(f"\nResultados en {OUT}")
    graficar(mom)


if __name__ == "__main__":
    main()
