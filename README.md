# Bitácora de la IA 
Buen día. Antes que nada quiero agradecer la oportunidad brindada a este proceso. Cabe aclarar que hace años no utilizaba python para ningún ambito más que correr pruebas automatizadas. Gracias al uso de la IA he podido realizar esto de una forma tan eficiente. Los retos más grandes encontrados fueron mentales e internos; la propuesta, preguntas están más que bien establecidas y es un reto formidable para un ingreso. Este reto me forzó a romper paradigmas impuestos por mi subconciente y pasado, por lo que estoy agradecido. Dicho esto.

El modelo utilizado fue Claude, un amigo me hizo el gran favor de prestarme una licencia que utiliza para aprende lenguajes de programación. Inicialmente al leer el reto pensé en botar la toalla porque pensé que era mucho más complicado que lo que mis habilidades me permitían; pero al incentivar el uso de la IA decidí retarme a mi mismo a resolver este reto a como fuese. Se comenzó el Domingo a las 2 de la tarde. 

El prompt más importante fue darle contexto a claude de lo que estoy buscando y la información que tengo; le doy contexto de mi falta de experiencia con algunos lenguajes y fortalezas en otros como SQL, al final decidí utilizar python por la versatilidad de leectura de documentos. 

Al identificar un plan de acción procedí a tratar de abrir y leer los archivos en VS Code, una herramienta que llevaba sin utilizar varios años, con ayuda de claude se realizaron iteraciones para poder abrir y leer los archivos y su información, al poderlos leer procedimos a comprender las preguntas proporcionadas en el reto y ver cual es el formato más optimo para la resolución del problema, tomando en cuenta calidad de datos en todo momento. 

se realizó un análisis y solución pregunta a pregunta del reto, no se respondieron las 4 de un prompt. Debido a mi falta de experiencia con Python, recaí bastante en la habilidad de la IA para poder programar, sin embargo se realizó una revisión de los calculos proporcionados dentro del código proporcionado y se hicieron las correcciones pertinentes tales como la detección y correción de devoluciones en el inventario.

Muchas veces el código proporcionado estaba muy recargado de información no relevante para la pregunta, por lo que se descartaban cálculos innecesarios y se trató de eficientizar el código.

# CaféNorte · Pipeline analítico
Pipeline que ingiere las fuentes de POS, ERP legacy y Shopify, las concilia en un
modelo dimensional y responde las 4 preguntas de negocio del reto.

```bash
pip install -r requirements.txt
python final.py        # pipeline completo: calidad, 4 respuestas, modelo, gráfica
python consultar.py    # ejecuta queries.sql contra el modelo persistido
pytest -v              # tests
```

Salidas en `outputs/`: seis CSV con las respuestas, `cafenorte.duckdb` con el
modelo consultable y `ventas_por_canal.png`.

---

## Stack y por qué

**pandas + DuckDB.** El dataset completo son ~330k filas entre las cuatro
fuentes: cabe holgadamente en memoria. Proponer Spark o un warehouse gestionado
aquí sería pagar complejidad operativa por un problema que no la necesita, y el
cliente tiene un presupuesto de USD $200/mes.

- **pandas** para ingesta y normalización. El JSON del ERP viene anidado con
  cuatro secciones heterogéneas y un `cost_history` por producto; aplanarlo es
  trabajo imperativo que en SQL quedaría ilegible. `merge_asof` resuelve el
  costo vigente por fecha en una línea.
- **DuckDB** para persistir el modelo analítico. Da SQL completo sobre archivos
  columnares sin servidor ni configuración, y las consultas que escribí son
  portables casi sin cambios a **Athena**, que es el motor de la arquitectura
  productiva propuesta. El modelo local y el de producción hablan el mismo SQL.

---

## Interpretaciones de las preguntas ambiguas

### P1 · Rotación de inventario

Para esta métrica no utilizo el costo del producto, ya que lo que quiero medir es cuántas veces se vende el inventario disponible en términos de unidades. El precio o costo del producto no debería cambiar la cantidad de veces que rota el inventario.

Uso una ventana de 6 meses porque es el periodo que realmente cubren los snapshots de inventario. Aunque el POS tiene información desde `2024-10-01`, los datos de inventario empiezan hasta `2025-10-01`. Si comparara 12 meses de ventas contra solamente 6 meses de inventario, estaría mezclando periodos diferentes y la rotación quedaría artificialmente inflada.

Para poder compararla con benchmarks anuales, anualizo el resultado de esos 6 meses proyectándolo linealmente a 12 meses. Sin embargo, hay que considerar que el periodo incluye la temporada navideña, por lo que la rotación anualizada probablemente esté un poco por encima de lo que observaríamos en un año completo.

También considero únicamente las ventas del canal físico. El e-commerce no tiene `tienda_id`, así que no es posible relacionar sus unidades vendidas con el inventario de las tiendas registrado en los snapshots.

### P2 · Quiebres de stock

Considero un quiebre de stock como una racha de días consecutivos en la que el inventario de un SKU en una tienda llega a cero. Por eso, si el inventario llega a cero durante algunos días, vuelve a tener existencias y posteriormente vuelve a cero, trato cada periodo como un quiebre independiente.

Los valores faltantes (`NaN`) no los interpreto como inventario cero. Si no existe un registro para un día, no podemos asumir que el inventario era cero. Por la misma razón, tampoco uno dos rachas de inventario en cero cuando entre ellas existen días sin información, porque no tenemos evidencia de qué ocurrió durante esos días.

El número mínimo de días para considerar un quiebre se puede modificar mediante `MIN_DIAS_QUIEBRE`. Para este análisis lo establecí en 3 días o más, aunque el enunciado menciona más de 3 días.

### P3 · Crecimiento MoM por canal

Las ventas de e-commerce están registradas en MXN, USD y EUR. Antes de calcular las ventas mensuales, convierto cada orden a MXN utilizando el tipo de cambio correspondiente al día de la venta, a partir de `exchange_rates.csv`. Esto evita sumar directamente cantidades de diferentes monedas como si fueran equivalentes.

El e-commerce comienza a registrar ventas en abril de 2025, así que el primer crecimiento MoM que puedo calcular es mayo contra abril. Los meses anteriores los dejo en cero y no los utilizo para calcular el crecimiento, porque todavía no existe un periodo previo de ventas de e-commerce con el cual hacer la comparación.

### P4 · Margen negativo

Para calcular el margen utilizo el costo que tenía el producto en el momento de la venta, no el costo actual. Para obtenerlo utilizo `merge_asof` sobre `cost_history`.

Esto es importante porque el costo de un producto puede cambiar con el tiempo. Si utilizara el costo actual para todas las ventas históricas, podría marcar como negativa una venta que en realidad sí era rentable cuando se realizó.

También asumo que `monto` representa el total de la línea de venta y no el precio unitario. Para las devoluciones, revierto tanto el ingreso como el costo, ya que el producto vuelve a formar parte del inventario.



---

## Conciliación entre fuentes

Las tres fuentes usan identificadores distintos para el mismo producto. La tabla
`sku_mappings` del ERP es el puente:

```
POS         sku (CN-000NN)    ──┐
ERP         sku_erp           ──┼──  sku_mappings  ──►  sku_erp (llave canónica)
Shopify     product_handle    ──┘
```

Ambos canales se unifican en `fact_ventas` con `sku_erp` como llave. El
e-commerce queda con `tienda_id` nulo y sin costo, porque el catálogo de costos
es del ERP y solo cubre tienda física.

**Modelo persistido:** `fact_ventas`, `fact_inventario`, `dim_tienda`,
`dim_producto`, `dim_costo`.

---

## Calidad de datos

El pipeline corre 11 verificaciones **antes** de publicar cualquier resultado.
Distingue dos niveles: un **ERROR** significa que el modelo se contradice a sí
mismo y aborta la ejecución; un **AVISO** es una limitación real de la fuente que
acota la lectura de una respuesta, pero el cálculo es correcto dentro de esa
limitación.

Hallazgos relevantes de la corrida actual:

| Hallazgo | Impacto |
|---|---|
| `tipo_comprobante` tiene 5 valores CFDI: I, E, T, P, N | Sumar `monto` sin distinguir infla las ventas **8.3%**: 30.9M reportados contra 28.4M reales |
| 3,079 notas de crédito (E) por 1.14M MXN | 4.0% de devoluciones sobre ventas brutas |
| 893 comprobantes T/P/N por 303k MXN | Traslados, pagos y **nómina** dentro del archivo de ventas: no son operaciones de venta |
| 10 SKUs del POS sin equivalente en el ERP | **14.3%** de las unidades vendidas quedan fuera de P1 y P4 |
| Parte de `cantidad_en_stock` viene como texto | Tratado como dato faltante, nunca como cero |
| 0 devoluciones en 9,947 órdenes de Shopify | Los refunds viven en un feed aparte: P3 sobreestima el ingreso neto digital |
| Las notas de crédito no referencian la venta original | No se puede atribuir una devolución al canal donde se compró |

---

## Respuestas

> Sustituir por las tablas de la corrida. Están en `outputs/*.csv`.

**P1 · Top 10 SKUs por rotación** → `outputs/p1_rotacion.csv`

**P2 · Tiendas con quiebres** → `outputs/p2_por_tienda.csv` y `p2_quiebres.csv`

**P3 · Crecimiento MoM por canal** → `outputs/p3_mom.csv` y
`ventas_por_canal.png`. Ventas físicas alrededor de 1.6–1.7M MXN mensuales;
e-commerce alrededor de 350k desde su arranque en abril de 2025.

**P4 · Productos con margen negativo** → `outputs/p4_margen_negativo.csv`

Dos productos pierden dinero, 276,441 MXN en total:

| Producto | Margen | % | Tiendas |
|---|---|---|---|
| Especial Café Molido | -211,719 MXN | -30.9% | 40 de 40 |
| Sándwich Comida Caliente | -64,723 MXN | -28.3% | 40 de 40 |

Ambos pierden alrededor de 30% en todas las tiendas, y no existe ningún caso
de producto rentable con pérdida localizada. Esa uniformidad apunta a un precio
de lista o un costo de catálogo mal configurados, no a un problema operativo de
ninguna sucursal. Es una conclusión más accionable que la lista de tiendas.

---

## Tests

```bash
pytest -v
```

Cubren tres cosas:

1. **Reconciliación contra la fuente cruda.** El ingreso del modelo debe ser
   exactamente la suma de comprobantes I menos los E del CSV original. Es la
   defensa directa contra un pipeline que corre sin error pero da números malos.
2. **Lógica de negocio sobre datos sintéticos.** Que una racha de 4 días se
   detecte y los ceros aislados no; que un `NaN` no una dos rachas separadas; que
   una venta anterior a un alza use el costo previo; que la conversión de moneda
   se aplique.
3. **Que el sistema de calidad detecte corrupción.** Se altera un ingreso a
   propósito y se verifica que la reconciliación marque ERROR. Un validador que
   nunca falla no está validando nada.

---

## Scope: qué dejé fuera y por qué

- **Sin orquestador.** Un `main()` secuencial es suficiente para cuatro fuentes
  y una corrida diaria. Airflow entra en la propuesta de producción, no aquí.
- **Sin carga incremental.** El pipeline reprocesa todo en cada corrida. Con este
  volumen tarda segundos y evita una clase entera de bugs de estado.
- **Sin dashboard.** Los resultados salen a CSV y a una gráfica. La capa de
  visualización es QuickSight en la arquitectura propuesta.
- **Validaciones adicionales identificadas pero no implementadas:** rangos de
  plausibilidad por categoría de producto, detección de outliers de precio, y
  reconciliación de unidades vendidas contra movimientos de inventario. Las
  documento en la propuesta como parte de la fase 2.

Ver `propuesta.md` para la arquitectura productiva en AWS y `AI_LOG.md` para la
bitácora de uso de IA.
