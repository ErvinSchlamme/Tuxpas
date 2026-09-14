-- Ventas netas por canal y mes (P3)
SELECT canal, date_trunc('month', fecha) AS mes,
       SUM(ingreso) AS ingreso_mxn, SUM(unidades) AS unidades
FROM fact_ventas GROUP BY 1, 2 ORDER BY 2, 1;

-- Productos con margen negativo (P4). Solo físico: el e-commerce no tiene costo.
SELECT p.nombre, p.categoria, SUM(v.ingreso) AS ingreso,
       SUM(v.margen) AS margen,
       SUM(v.margen) / SUM(v.ingreso) * 100 AS margen_pct,
       COUNT(DISTINCT v.tienda_id) AS tiendas
FROM fact_ventas v JOIN dim_producto p USING (sku_erp)
WHERE v.canal = 'fisico'
GROUP BY 1, 2 HAVING SUM(v.margen) < 0 ORDER BY margen;

-- Rotación anualizada por SKU (P1)
WITH vendido AS (
    SELECT sku_erp, SUM(unidades) AS unidades
    FROM fact_ventas WHERE canal = 'fisico' GROUP BY 1
), stock AS (
    SELECT sku_erp, AVG(total) AS inv_promedio FROM (
        SELECT fecha, sku_erp, SUM(cantidad_en_stock) AS total
        FROM fact_inventario GROUP BY 1, 2) GROUP BY 1
)
SELECT p.nombre, v.unidades, s.inv_promedio,
       v.unidades / s.inv_promedio * (365.0 / 182) AS rotacion_anual
FROM vendido v JOIN stock s USING (sku_erp) JOIN dim_producto p USING (sku_erp)
WHERE s.inv_promedio > 0 ORDER BY rotacion_anual DESC LIMIT 10;