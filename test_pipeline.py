"""Tests del pipeline CaféNorte. Ejecutar: pytest -v"""

import pandas as pd
import pytest

from final import (TIPOS_NO_VENTA, TIPOS_VENTA, cargar, normalizar_ecom,
                   normalizar_pos, p2_quiebres, verificar_calidad)


@pytest.fixture(scope="module")
def d():
    return cargar()


@pytest.fixture(scope="module")
def ventas(d):
    return normalizar_pos(d["pos"], d["mapeo"], d["costos"])


@pytest.fixture(scope="module")
def ecom(d):
    return normalizar_ecom(d["ecom"], d["fx"])


# ───────────────────── reconciliación contra la fuente cruda

def test_ingreso_cuadra_con_la_fuente(d, ventas):
    """El ingreso del modelo debe ser exactamente ventas I menos notas E."""
    pos = d["pos"]
    esperado = (pos.loc[pos.tipo_comprobante == "I", "monto"].sum()
                - pos.loc[pos.tipo_comprobante == "E", "monto"].sum())
    assert ventas.ingreso.sum() == pytest.approx(esperado, rel=1e-9)


def test_comprobantes_no_venta_excluidos(d, ventas):
    assert d["pos"].tipo_comprobante.isin(TIPOS_NO_VENTA).sum() > 0
    assert not ventas.tipo_comprobante.isin(TIPOS_NO_VENTA).any()


def test_devoluciones_restan(ventas):
    dev = ventas[ventas.es_devolucion]
    assert len(dev) > 0
    assert (dev.unidades < 0).all() and (dev.ingreso < 0).all()


def test_merge_no_duplica_filas(d, ventas, ecom):
    assert len(ventas) == d["pos"].tipo_comprobante.isin(TIPOS_VENTA).sum()
    assert len(ecom) == len(d["ecom"])
    assert ventas.venta_id.duplicated().sum() == 0


# ───────────────────── lógica de negocio con datos sintéticos

def test_racha_consecutiva_se_detecta():
    """4 días seguidos en cero = 1 quiebre; ceros aislados no cuentan."""
    stock = [5] * 20
    for i in (4, 5, 6, 7):
        stock[i] = 0
    stock[11] = stock[13] = 0
    snaps = pd.DataFrame({"fecha": pd.date_range("2026-01-01", periods=20),
                          "tienda_id": "T001", "sku_erp": "SKU-A",
                          "cantidad_en_stock": stock})

    quiebres, _, _ = p2_quiebres(snaps)
    assert len(quiebres) == 1
    assert quiebres.iloc[0].dias == 4


def test_faltante_no_cuenta_como_quiebre():
    """Un NaN no es stock cero: no inicia racha ni une dos separadas."""
    snaps = pd.DataFrame({"fecha": pd.date_range("2026-01-01", periods=6),
                          "tienda_id": "T001", "sku_erp": "SKU-A",
                          "cantidad_en_stock": [0, 0, None, 0, 0, 5]})
    quiebres, _, _ = p2_quiebres(snaps)
    assert quiebres.empty


def test_costo_vigente_no_es_el_actual(d, ventas):
    """Ventas previas a un alza deben usar el costo anterior, no el actual."""
    con_costo = ventas.dropna(subset=["costo_mxn"])
    hist = d["costos"]
    multi = hist.groupby("sku_erp").size()
    sku = multi[multi > 1].index[0]

    vigencias = hist[hist.sku_erp == sku].sort_values("fecha_vigencia")
    corte = vigencias.fecha_vigencia.iloc[-1]
    ultimo_costo = vigencias.costo_mxn.iloc[-1]

    previas = con_costo[(con_costo.sku_erp == sku)
                        & (con_costo.fecha_hora < corte)]
    if previas.empty:
        pytest.skip("No hay ventas anteriores al último cambio de costo")
    assert (previas.costo_mxn != ultimo_costo).all()


def test_margen_es_ingreso_menos_costo(ventas):
    v = ventas.dropna(subset=["costo_mxn"])
    assert (v.margen - (v.ingreso - v.costo_total)).abs().max() < 1e-6


def test_conversion_fx_aplicada(ecom):
    """Una orden en USD vale más en MXN; una en MXN queda igual."""
    usd = ecom[ecom.currency == "USD"]
    assert len(usd) > 0
    assert (usd.ingreso > usd.amount).all()
    mxn = ecom[ecom.currency == "MXN"]
    assert (mxn.ingreso == mxn.amount).all()


# ───────────────────── el sistema de calidad se autovalida

def test_calidad_sin_errores(d, ventas, ecom):
    errores = [c for c in verificar_calidad(d, ventas, ecom)
               if c.estado == "ERROR"]
    assert not errores, [c.nombre for c in errores]


def test_calidad_detecta_ingreso_corrupto(d, ventas, ecom):
    """Si el ingreso se altera, la reconciliación debe marcar ERROR."""
    corrupto = ventas.copy()
    corrupto.loc[corrupto.index[0], "ingreso"] += 1000
    ch = [c for c in verificar_calidad(d, corrupto, ecom)
          if c.nombre == "Reconciliación vs fuente"][0]
    assert ch.estado == "ERROR"