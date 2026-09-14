# Propuesta técnica · Plataforma de datos CaféNorte

**Para:** Dirección General y Dirección de TI, CaféNorte
**Asunto:** Un solo lugar para ver ventas y rotación de las 40 tiendas y el canal digital

---

## Lo que encontramos en sus datos

Antes de proponer infraestructura, procesamos sus tres fuentes. Tres hallazgos
explican por qué cada área les reporta un número distinto:

**Sus ventas están sobreestimadas 8.3%.** El archivo del punto de venta mezcla
cinco tipos de comprobante fiscal. Además de las ventas (I), contiene notas de
crédito (E), traslados entre tiendas (T), complementos de pago (P) y hasta
registros de nómina (N). Quien sume la columna de monto sin distinguir reporta
30.9 millones; la cifra correcta es 28.4 millones.

**El 14.3% de lo que venden no existe en el catálogo del ERP.** Diez SKUs se
venden en tienda pero no tienen equivalente en el sistema de inventario, así que
hoy no es posible calcular su rotación ni su margen.

**Dos productos les están costando 276 mil pesos.** Café Molido Especial y el
Sándwich de comida caliente se venden por debajo de su costo, con una pérdida
cercana al 30% en **las 40 tiendas**. Que sea uniforme en toda la red indica un
error de precio de lista o de costo en catálogo, no un problema de alguna
sucursal: se corrige en un solo lugar.

Esto es lo que se puede ver con una foto de sus datos. La plataforma lo convierte
en algo que se actualiza solo, todos los días.

---

## Arquitectura propuesta

```
  POS (CSV)  ──┐
  ERP (JSON) ──┼──►  S3 raw  ──►  AWS Glue  ──►  S3 curated   ──►  Athena  ──►  QuickSight
  Shopify    ──┘     (landing)    (ingesta y     (Parquet          (SQL)        (dashboard)
  Tipos de cambio                 normalización)  particionado)
                                       │                │
                                  EventBridge      Glue Data Catalog
                                  (diario 5am)      (esquema)
```

| Servicio | Rol | Por qué este y no otro |
|---|---|---|
| **S3** | Repositorio único de datos, crudo y procesado | Almacenamiento a centavos por GB; separa el dato de quien lo procesa |
| **Glue (Python Shell)** | Ingesta, limpieza y conciliación entre sistemas | Serverless: pagan los minutos que corre, no un servidor encendido |
| **Glue Data Catalog** | Diccionario de tablas y columnas | Gratis en su volumen; cualquier herramienta AWS lo lee |
| **Athena** | Motor de consultas SQL | Sin servidor. Cobra por dato leído, no por tiempo encendido |
| **EventBridge** | Dispara la actualización diaria | Sustituye un orquestador dedicado a costo cero |
| **QuickSight** | Tablero para dirección y operación | Integrado con Athena; no requiere licencias de BI externas |

**La decisión de fondo:** todo el stack es *serverless*. Con 40 tiendas y un
canal digital, su volumen de datos es de unos cuantos gigabytes al año. Pagar un
warehouse encendido las 24 horas sería pagar capacidad que no van a usar.

---

## Costo mensual estimado

| Concepto | Supuesto | USD/mes |
|---|---|---|
| S3 | 20 GB con histórico y versiones | $1 |
| Glue | 1 corrida diaria, ~5 min, 1 DPU | $2 |
| Athena | 30 GB escaneados al mes (Parquet particionado) | $1 |
| Glue Data Catalog | Bajo el millón de objetos | $0 |
| EventBridge + Lambda | Dentro de capa gratuita | $0 |
| CloudWatch | Logs y alertas de fallo | $5 |
| Secrets Manager | Credenciales de Shopify y ERP | $1 |
| **Subtotal infraestructura** | | **~$10** |
| QuickSight | 2 autores + 15 lectores | $93 |
| **Total** | | **~$103** |

Queda alrededor de **$97 de holgura** sobre los $200 disponibles, suficiente para
absorber crecimiento o usuarios adicionales. Cifras de referencia para us-east-1;
conviene validarlas en la Calculadora de Precios de AWS al momento de firmar.

**El costo lo domina QuickSight, no la infraestructura.** Cada lector adicional
suma unos $3 al mes. Por eso una de nuestras preguntas es cuántas personas
necesitan acceso al tablero.

**Qué descartamos, con números:**

- **Redshift Serverless** factura un mínimo de 8 unidades de cómputo. Con tres
  horas de uso al día son unos **$259 mensuales solo de cómputo**: supera el
  presupuesto completo antes de contar nada más.
- **MWAA (Airflow gestionado)** arranca en torno a **$350 mensuales** para el
  ambiente más pequeño. EventBridge hace lo que ustedes necesitan hoy por cero.
- **Herramientas de ingesta gestionadas** (tipo Fivetran) consumirían por sí
  solas la mitad del presupuesto para cuatro fuentes que cambian poco.

---

## Plan de implementación

**Fase 1 · Fundación (semanas 1–3) — el tablero que pidió el dueño**

Ingesta automática de las tres fuentes a S3, modelo unificado de ventas e
inventario, y tablero con ventas por tienda y canal, rotación y alertas de
quiebre de stock. Al final de esta fase existe el "un solo lugar" y las cuatro
preguntas se responden solas cada mañana.

*Riesgo:* el acceso al ERP legacy. Si no expone API y solo genera archivos, hay
que acordar un mecanismo de entrega confiable. Es la dependencia que más puede
mover la fecha.

**Fase 2 · Confianza en el dato (semanas 4–6)**

Validaciones automáticas que detienen la publicación si los números no cuadran
contra el origen, alertas por correo cuando algo falla, y resolución de los 10
SKUs sin mapeo. Sin esta fase, el tablero muestra números que nadie puede
auditar.

*Riesgo:* el mapeo de SKUs requiere decisiones de negocio de su gente, no
técnicas. Necesitamos un responsable de su lado.

**Fase 3 · Profundidad (semanas 7–10)**

Márgenes por tienda y categoría, integración de los reembolsos de Shopify que hoy
no llegan, y análisis de estacionalidad por región. Aquí es donde la plataforma
empieza a responder preguntas que hoy no se pueden ni formular.

*Riesgo:* Shopify exporta los reembolsos en un feed distinto al de órdenes.
Integrarlo es trabajo adicional que hoy no está dimensionado.

---

## Supuestos

1. El volumen actual (~86 mil tickets y ~10 mil órdenes en 18 meses) crece a
   ritmo similar. Un salto de 10x cambiaría la arquitectura.
2. Una actualización diaria es suficiente. Si requieren datos en tiempo real, es
   otra conversación y otro presupuesto.
3. La cuenta AWS existente puede alojar esto sin conflicto con otras cargas.
4. Los montos del POS no incluyen IVA desglosado.
5. Los tipos de cambio del archivo que recibimos son la fuente oficial para
   convertir ventas internacionales.

## Preguntas antes de firmar

1. **¿Cuántas personas van a consultar el tablero?** Es la variable que más mueve
   el costo mensual.
2. **¿Se aceptan en tienda devoluciones de compras hechas en línea?** Sus notas
   de crédito no referencian la venta original, así que hoy toda devolución se
   atribuye al canal físico. Si hay devoluciones cruzadas, la rentabilidad por
   canal está distorsionada.
3. **Los 10 SKUs sin equivalente en el ERP, ¿son productos nuevos sin dar de alta
   o descontinuados que siguen en el punto de venta?**
4. **¿Por qué hay comprobantes de nómina en el archivo de ventas?** Sugiere que
   la exportación del POS no está filtrando por tipo de documento.
5. **¿Su ERP conserva movimientos de inventario o solo fotos diarias?** Los
   movimientos permitirían medir mermas y no solo quiebres.
6. **¿Quién es el dueño del dato?** Necesitamos una persona que pueda decidir
   cuándo dos sistemas se contradicen.

---

*Quedamos atentos para revisar cualquier punto antes de formalizar el alcance.*
