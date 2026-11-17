# Evaluación sintética de respuestas y fuentes

## Objetivo y método

Comparar la versión anterior del generador con la versión que exige citas
textuales comprobables. Una comparación válida requiere el mismo PDF, preguntas,
configuración y carga de consultas; los recorridos ya realizados difieren en
condiciones y el PDF inicial era ambiguo. Cada ejecución genera una ingesta
independiente en Qdrant temporal; OCR, modelo y red pueden producir variaciones.
Una sola ejecución por versión no demuestra una mejora estadísticamente significativa.

### Línea base observada

La primera ejecución de 15 casos devolvió 11 respuestas y **4 errores HTTP
503**; se desconocen sus causas y no se deben atribuir a cuota sin evidencia. De
las 11 respuestas, siete contestaron correctamente preguntas respondibles con
las páginas esperadas, una se abstuvo ante una pregunta que tenía respuesta
(fecha de Cedar Lantern) y tres se abstuvieron correctamente ante información
ausente. La cuarta pregunta sin evidencia tuvo error técnico. En las siete
respuestas correctas, las ocho páginas citadas coincidieron con la anotación de
esas respuestas; no hubo citas adicionales observadas en ese subconjunto. Las
cuatro solicitudes con error no se califican como respuestas ni abstenciones.
La línea base está incompleta y no debe usarse como estimación de calidad general.

### Primera ejecución con citas literales

Se intentaron los mismos 15 casos: seis obtuvieron respuesta y nueve terminaron
con error HTTP 503. Los diagnósticos del proveedor identificaron ocho respuestas
HTTP 429 y una respuesta de formato inválido; no se conserva ni se registra el
contenido de la respuesta inválida. De los seis resultados, cuatro respondieron
correctamente con la página esperada y dos se abstuvieron correctamente ante
información ausente; no se observaron fuentes adicionales en esos cuatro casos.
Las nueve solicitudes con error no se califican. El resultado no permite afirmar
una mejora frente a la línea base, por los errores, el tamaño pequeño y la
variabilidad de cada ejecución.

Después de esa ejecución se distinguió el límite del proveedor como HTTP 429
(sin reintento automático) y el guion pasó a espaciar solicitudes. Esto cambia
las condiciones de carga respecto a la línea base: debe considerarse una
limitación al comparar recorridos. La prueba se detiene después de dos 429
consecutivos para no seguir consumiendo solicitudes mientras exista el límite.

En la siguiente ejecución espaciada se intentaron cuatro de los 15 casos: uno
respondió correctamente con la página esperada y tres recibieron HTTP 429. La
prueba se detuvo tras los dos 429 consecutivos finales. No se sabe si el límite
era por minuto, por día, por modelo u otra cuota del proyecto; el código no
registró el cuerpo de la respuesta del proveedor. No se completó la evaluación
ni se puede comparar calidad con la línea base. Antes de repetir, el operador
debe revisar la cuota y disponibilidad del modelo en Google AI Studio.

### Ejecución posterior con clave actualizada

Con la clave actualizada y solicitudes espaciadas se intentaron los 15 casos sin
observar HTTP 429. Cinco terminaron en HTTP 503 por respuestas que el adaptador
clasificó como formato inválido; los diagnósticos disponibles en esa ejecución
no permiten identificar todavía la causa exacta. De los diez resultados restantes,
cinco respondieron correctamente con las páginas esperadas, uno se abstuvo ante
la fecha de Cedar Lantern y cuatro se abstuvieron correctamente. En las cinco
respuestas correctas se citaron seis páginas, todas esperadas; esto no permite
concluir que se haya mejorado la precisión, porque hay cinco errores y la línea
base también fue incompleta. La página del calendario no nombra explícitamente
Cedar Lantern: la asociación esperada depende de la continuidad del documento y
es una ambigüedad adicional del caso de prueba.

Una reproducción posterior de tres casos produjo una respuesta válida y dos
respuestas incompletas. En ambas, Gemini indicó `MAX_TOKENS` y el contenido no
era JSON completo. Esto identifica el límite de generación como causa de esas
dos reproducciones, pero no demuestra la causa de los cinco errores anteriores.
Se añadió diagnóstico sin registrar texto del proveedor ni datos de entrada.
Puede probarse un límite temporal distinto, sin modificar `.env`, con
`SMOKE_GEMINI_MAX_OUTPUT_TOKENS=2048`; este cambio puede incrementar consumo de
tokens y no garantiza eliminar los cortes. Las condiciones son distintas de la
línea base y la comparación debe indicarlo.

Con un límite temporal de **2048 tokens** en el contenedor de prueba se
repitieron tres casos: uno recibió `429`, uno respondió correctamente citando
las páginas 1 y 2, y uno se abstuvo. No se observaron `MAX_TOKENS` en las dos
respuestas recibidas, pero la muestra no demuestra que 2048 sea suficiente para
todas las preguntas. El `.env` del usuario no se modificó.

**Corrección de la anotación:** la página 2 dice «project schedule» sin nombrar
explícitamente Cedar Lantern. Por tanto, citar también la página 1 puede ser
necesario para justificar esa asociación entre páginas. No debe calificarse
automáticamente la página 1 como cita adicional incorrecta para Cedar en la
página 2. La anotación original `[2]` para `cedar_launch` y `cedar_meeting` es
ambigua; se conserva el registro histórico y se excluyen esas citas de una métrica
de precisión de fuentes hasta preparar y ejecutar una versión inequívoca del PDF.
El guion ofrece `SMOKE_GEMINI_DATASET=v1` para reproducir el original y `v2`
(predeterminado), cuya página 2 nombra explícitamente Cedar Lantern. Los
resultados de v1 y v2 no son directamente comparables.

### Gemini 3.5 Flash Lite con PDF v2

Se ejecutaron las 15 preguntas con `gemini-3.5-flash-lite`, `maxOutputTokens=512`
y el PDF v2, con las consultas espaciadas. El recorrido HTTP/OCR/embeddings/
Qdrant/SQLite/Gemini fue real y terminó sin errores técnicos. Según la revisión
del asistente contra la anotación sintética:

- Las 11 preguntas con información produjeron la respuesta esperada.
- Las cuatro preguntas sin información produjeron abstención y ninguna fuente.
- Las 14 referencias de página mostradas para las 11 respuestas correspondían a
  las páginas esperadas: no se observaron páginas adicionales ni omitidas en
  este conjunto. Las citas textuales internas pasaron la validación exacta.

Esta es una evaluación de una sola ejecución en un PDF sintético, no una revisión
humana ni una medición general. No demuestra que las citas respalden
semánticamente cada afirmación para otros documentos. No es una comparación
controlada con las ejecuciones previas, porque cambió el modelo y se corrigió la
ambigüedad del PDF, además de la diferencia de carga y los errores previos.

La evaluación usa 15 preguntas sobre un PDF sintético de tres páginas, sin datos
de usuarios. El script muestra cada respuesta, sus fuentes y la anotación
esperada; no pretende evaluar automáticamente la verdad semántica. Una persona
debe comparar cada resultado con la tabla y el documento sintético. Los errores
HTTP se cuentan por separado y nunca se tratan como abstenciones correctas.

## Referencia del PDF

| Página | Contenido relevante |
|---|---|
| 1 | Cedar Lantern; coordinadora Nora Vega; presupuesto aprobado 2400 dólares. |
| 2 | En v2 se identifica como Cedar Lantern: lanzamiento el 17 de marzo de 2027; reunión el martes por la mañana. En v1 no se nombraba explícitamente el proyecto. |
| 3 | Maple Harbor; coordinador Leo Marin; presupuesto aprobado 5100 dólares; lanzamiento el 22 de abril de 2027. |

Las preguntas cubren hechos de una página, respuestas que combinan dos páginas,
proyectos parecidos y cuatro preguntas sin información: teléfono, dirección,
gasto real (no presupuesto aprobado) y ciudad de lanzamiento. Las páginas
esperadas están definidas junto a cada pregunta en `verificar_gemini_real.py`.
Para respuestas sin información esperamos abstención, sin fuentes que aparenten
respaldar una respuesta inventada.

## Registro y métricas

Por pregunta se registra: respuesta correcta/incorrecta/abstención, páginas
citadas, páginas esperadas y error técnico. Informar tanto los resultados
individuales como estas cantidades:

- **Citas pertinentes:** páginas citadas que respaldan la respuesta esperada.
- **Citas adicionales:** páginas citadas que no son necesarias para esa respuesta.
- **Citas omitidas:** páginas necesarias no citadas en respuestas multipartes.
- **Respuestas acertadas y abstenciones correctas:** contadas por separado.

Un enlace a un fragmento y una cita textual que aparece en él no demuestran que
la afirmación se derive lógicamente de esa cita. La revisión humana es necesaria.
Los números de una muestra de 15 preguntas no generalizan a PDFs arbitrarios,
otros idiomas, modelos, documentos o adversarios.

## Ejecución

```bash
SMOKE_IMAGE=document-intelligence-stage-preview:local bash scripts/smoke/gemini_real.sh
```

Hace llamadas reales a Gemini, puede consumir cuota y envía exclusivamente las
preguntas y textos sintéticos. Usa una red con salida externa sin filtrado por
dominio, contenedores sin puertos publicados y código local montado de solo
lectura. La clave no se imprime, pero el entorno de Docker puede ser consultado
por usuarios privilegiados. No modifica ni reinicia servicios Compose existentes.
El intervalo por defecto entre inicios de consultas es de 15 segundos y puede
cambiarse con `SMOKE_GEMINI_INTERVAL_SECONDS` entre 0 y 120; es regulación de la
prueba, no un reintento ni una garantía contra los límites de Google.
