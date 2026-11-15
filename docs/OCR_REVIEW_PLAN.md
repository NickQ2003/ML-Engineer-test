# Plan de decisiones — hallazgos del OCR

**Fecha:** 2026-11-15

**Estado:** correcciones implementadas a nivel de servicio; integración HTTP y
límites globales pendientes.

**Alcance:** servicio OCR y su ejecución asíncrona, antes de integrarlos en
`/api/extract`. Los puntos siguientes conservan la evidencia y decisiones de la
revisión; el estado de implementación se resume en la tabla.

## Objetivo

Corregir los problemas que pueden afectar la ubicación de nombres, los recursos
y la confidencialidad de los documentos, conservando el contrato HTTP exigido
por la prueba. Las alternativas indicadas son recomendaciones para implementar y
validar; cualquier cambio de diseño debe registrarse en `DECISIONS.md`.

## Resumen y orden propuesto

| Orden | ID | Prioridad | Hallazgo | Estado |
|---|---|---|---|---|
| 1 | R-01 | Alta | Un PDF pequeño puede producir una imagen enorme | Corregido en servicio; umbral por calibrar |
| 2 | R-02 | Alta | La cancelación directa puede dejar Tesseract y archivos temporales | Corregido en cierre/timeout controlados en POSIX |
| 3 | R-03 | Alta | La rotación de metadatos puede corregir una imagen ya girada | Mitigado mediante heurística; precisión no garantizada |
| 4 | R-04 | Media | El límite de procesos no limita la cola ni las copias de PDF | Parcial: servicio acotado; falta admisión HTTP y límite global |
| 5 | R-05 | Media | El proveedor con caché puede crear varias instancias al inicio concurrente | Corregido en ciclo de vida; falta usarlo en el endpoint real |
| 6 | R-06 | Baja | `LOG_LEVEL` se configura pero no se aplica | Corregido y probado |

La admisión antes de procesar multipart y el uso de la dependencia compartida
deben verificarse al implementar el endpoint real. Los límites siguen siendo por
proceso y no hay garantía de limpieza ante una caída abrupta de toda la máquina.

## Implementación posterior a la revisión

- **R-01:** `MAX_PAGE_PIXELS=20000000` por defecto; se estima el área antes de
  renderizar y se rechazan páginas excesivas con `PDFPageTooLargeError`. El
  umbral aún debe calibrarse con las restricciones de memoria del despliegue.
- **R-02:** subprocessos POSIX supervisados en grupos separados y directorios
  temporales privados. Una solicitud cancelada no abandona un trabajo ya iniciado;
  el timeout total o el cierre controlado terminan el grupo. Pruebas con un
  subprocesso simulado que ignora `SIGTERM` verifican que no quede activo ni
  queden temporales. Una caída abrupta o un `SIGKILL` del contenedor no ofrece
  garantías de limpieza; falta verificar el comportamiento en el endpoint real.
- **R-03:** OCR en orientación visible y segunda pasada si la confianza media
  es inferior al umbral configurable; se elige la mayor confianza. Se prueban
  OCR real y cuadros con escaneos en ambas orientaciones, con recorte y cuatro
  rotaciones. La confianza es una heurística, no una garantía de precisión.
- **R-04:** límites por proceso de trabajos activos y en cola, espera máxima,
  timeout de lectura y `extract_stream_async()` que adquiere admisión antes de
  leer. Un llamador que entregue `bytes` ya los habrá acumulado; el futuro HTTP
  debe controlar la carga antes del parseo multipart y manejar saturación.
- **R-05:** el servicio se crea en el ciclo de vida FastAPI y se obtiene por
  `app.api.dependencies.get_ocr_service`. Se prueba con solicitudes concurrentes
  sobre una ruta de prueba; `/api/extract` todavía no existe.
- **R-06:** se valida y aplica `LOG_LEVEL` a los loggers de aplicación y Uvicorn;
  los logs propios de aplicación se emiten como JSON. Se prueba el nivel y la salida.

## R-01 — Límite de píxeles renderizados

**Evidencia:** un PDF de 522 bytes con una página de 10.000 × 10.000 puntos llegó
al renderizado. A 200 DPI supone aproximadamente 771,6 millones de píxeles y
2,31 GB para una sola copia RGB; no se realizó esa reserva durante la revisión.

**Decisión recomendada:** añadir un máximo configurable de píxeles por página.
Calcular las dimensiones renderizadas, de forma conservadora, antes de llamar a
`get_pixmap()`; rechazar páginas que excedan el límite con un error específico.
Empezar evaluando un límite de 20 millones de píxeles y ajustarlo a la memoria
real disponible, considerando que cada trabajo puede crear varias copias y que
hay más de un proceso concurrente. El número definitivo requiere medición.

**Alternativa:** reducir automáticamente el DPI. Es menos predecible para la
precisión del OCR; solo adoptarla si se define y prueba un mínimo aceptable.

**Criterios de aceptación:**

- Una página enorme se rechaza antes de reservar la imagen.
- Se prueban los límites, dimensiones no válidas y DPI configurables.
- Los PDF de ejemplo siguen procesándose; se documenta el error de cara a HTTP.

## R-02 — Cancelación y limpieza de procesos

**Evidencia:** con `asyncio.Task.cancel()` se observó la terminación del proceso
trabajador mientras un subprocesso simulado de Tesseract seguía activo y dos
archivos temporales permanecían. La prueba actual cubre cancelación mediante
AnyIO, no este caso. El subprocesso simulado se terminó al finalizar la prueba.

**Decisión recomendada:** supervisar los trabajos fuera del ámbito de vida de
una solicitud individual. Una solicitud cancelada mientras espera capacidad no
debe iniciar OCR; si el trabajo ya empezó, mantenerlo supervisado hasta su fin o
hasta un límite total explícito. Conservar el permiso de concurrencia mientras
el trabajo siga vivo. Definir una estrategia de parada y limpieza del árbol de
procesos y de un directorio temporal privado por trabajo, también ante cierre
del servidor o terminación forzada del worker.

**Alternativa:** cancelar inmediatamente el trabajo. Requiere controlar la
terminación de Tesseract y sus descendientes antes de liberar capacidad, además
de limpiar temporales; matar solo el worker no es suficiente.

**Criterios de aceptación:**

- Probar cancelación directa `asyncio.Task.cancel()`, cancelación AnyIO y tareas
  canceladas en cola; no quedan subprocessos ni archivos temporales huérfanos.
- Verificar que el límite de trabajos sigue respetándose durante cancelaciones.
- Probar el comportamiento en cierre del servidor y documentar límites y
  excepciones, sin afirmar garantías que no se puedan cumplir ante caída total.
- Mantener un tiempo máximo razonable para el documento completo, además del
  timeout individual de Tesseract por página.

## R-03 — Orientación de PDF y escaneo

**Evidencia:** un PDF con la imagen físicamente girada y metadatos que la hacen
visible en posición correcta produjo `ALICE SMITH` al aplicar OCR a la página
mostrada, pero el servicio devolvió texto incorrecto al deshacer la rotación.

**Decisión recomendada:** evaluar primero la página en su orientación visible y
definir una alternativa de orientación cuando haya señales de baja calidad.
Comparar las opciones de forma explícita y conservar la transformación que
permita devolver los cuadros en puntos del recorte sin rotar. Una segunda pasada
puede aumentar el tiempo y no garantiza que una puntuación de confianza detecte
todos los errores; medir el comportamiento con ejemplos representativos.

**Alternativas:** confiar siempre en metadatos o siempre en la imagen sin rotar.
Las dos fallan en algunas combinaciones de orientación física y metadatos.

**Criterios de aceptación:**

- OCR real sobre contenido horizontal con metadatos de rotación y sobre imagen
  físicamente girada cuya rotación de metadatos corrige la vista.
- Comprobar texto y cuadros para 0°, 90°, 180° y 270°, con y sin recorte.
- Mantener documentada la limitación respecto a inclinación arbitraria.

## R-04 — Admisión y cola acotada

**Evidencia:** con la capacidad ocupada, cuatro entradas de 1 MB se serializaron
antes de poder iniciar un worker. El limitador actual restringe procesos, no el
número de solicitudes ni las copias de datos en espera.

**Decisión recomendada:** establecer admisión acotada antes de acumular la carga
completa en el futuro endpoint, limitar también la espera por capacidad y usar
una respuesta HTTP documentada cuando no haya cupo. En la capa de servicio,
adquirir capacidad antes de cualquier serialización grande y evaluar opciones
para no bloquear el bucle al copiar datos. Conservar el límite por proceso como
protección local y documentar que varios workers multiplican la capacidad total.

**Criterios de aceptación:**

- Pruebas con más solicitudes que la capacidad, entradas grandes y cancelación.
- La salud de la API sigue respondiendo bajo carga; la cola tiene un límite claro.
- No se acumulan cargas completas sin límite; se prueban errores y liberación
  de capacidad. Definir una política global si se usan varios workers o réplicas.

## R-05 — Instancia compartida del servicio

**Evidencia:** cuatro primeras llamadas concurrentes desde hilos a la función
decorada con `lru_cache` crearon cuatro instancias en una prueba controlada.
El problema afectaría a una integración que la llame concurrentemente desde
hilos, por ejemplo como dependencia síncrona; aún no existe `/api/extract`.

**Decisión recomendada:** crear el servicio una vez en el ciclo de vida de
FastAPI y recuperarlo mediante `app.state`, manteniendo inyección sustituible en
pruebas. Evitar depender de `lru_cache` como garantía de inicialización única.

**Criterios de aceptación:** pruebas de inicialización concurrente y del límite
compartido usando la dependencia HTTP real cuando se implemente el endpoint.

## R-06 — Configuración de logs

**Evidencia:** `LOG_LEVEL` se incluye en Compose y `Settings`, pero no se usa
para configurar logging o Uvicorn.

**Decisión recomendada:** conectarlo a la configuración efectiva de logs y
validar los valores admitidos; si no se implementa, no presentarlo como opción
operativa. No registrar credenciales ni contenido sensible de documentos.

**Criterio de aceptación:** una prueba cambia el nivel y verifica qué registros
se emiten; la documentación refleja el comportamiento real.

## Secuencia de entrega

1. Corregir R-01 y R-02 con pruebas y una entrada por cambio en la bitácora.
2. Corregir R-03 y comprobar cuadros con OCR real y fixtures de ambos casos.
3. Preparar R-04 y R-05 al integrar el endpoint, antes de exponerlo.
4. Resolver R-06 y completar observabilidad sin registrar datos sensibles.
5. Ejecutar toda la batería en la imagen reconstruida, registrar resultados y
   actualizar `DECISIONS.md`. No marcar los hallazgos como resueltos hasta pasar
   sus criterios de aceptación.
