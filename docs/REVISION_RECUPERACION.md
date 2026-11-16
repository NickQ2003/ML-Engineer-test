# Revisión: recuperación de nombres y vista temporal

## Alcance y calificación

La implementación previa del recuperador se califica provisionalmente con
**6/10 como solución de ingeniería para este caso**, no como porcentaje de
precisión o cobertura. La calificación corresponde al estado anterior a las
correcciones descritas abajo; no es una valoración del autor ni una medición de
exactitud sobre una muestra representativa.

Aspectos positivos: desactivación real con valor `0`, límites de trabajo,
reutilización de OCR y NER, validación geométrica y pruebas automatizadas. Los
principales problemas detectados fueron:

1. **Omisión reproducible con viñeta.** En el PDF de ejemplo OCR reconoció tres
   apariciones de «Elizabeth Park»; NER localizó una. La recuperación anterior
   añadía solo una: en la tercera posición consideraba `- Elizabeth Park,` y
   `Elizabeth Park,` como ventanas diferentes pero equivalentes y las descartaba
   como ambiguas.
2. **Fuzzy global demasiado restrictivo para el objetivo solicitado.** La
   comparación exigía una sola edición en todo el nombre y un resultado global
   de 0,90; no evaluaba cada componente de forma independiente.
3. **Verificación incompleta.** Los 421 tests anteriores y el conteo de cuadros
   no eran una medición de precisión ni comprobaban las tres apariciones del
   nombre observado. MT-002 no estaba aprobada.

## Correcciones realizadas

- Las ventanas de búsqueda no pueden comenzar ni terminar con una palabra OCR
  formada solo por puntuación. Esto elimina la falsa ambigüedad causada por la
  viñeta sin descartar alternativas realmente distintas.
- El nivel 2 compara cada componente en orden: máximo una edición por
  componente, hasta dos componentes modificados y puntuación mínima de 0,80 por
  componente modificado. Los componentes de tres caracteres o menos deben
  coincidir exactamente. Se exige el nombre completo, no se atribuyen palabras
  sueltas a personas, y los candidatos ambiguos se descartan.
- El umbral existente `FUZZY_THRESHOLD` sigue siendo independiente. La
  recuperación continúa desactivada por defecto y solo usa referencias NER
  localizadas y cuadros OCR de la misma solicitud.
- Se añadió una prueba de regresión con OCR y spaCy reales que comprueba las
  tres apariciones de ese nombre en el PDF de ejemplo, además de pruebas con
  omisiones NER simuladas, componentes parecidos y puntuación marginal.

## Vista temporal para la futura interfaz

La vista es opcional y está desactivada por defecto. Al activarla con una clave,
conserva en memoria un único proxy PDF y los datos de cuadros de OCR, NER, cada
pasada de recuperación habilitada y el resultado final. La API protegida permite
consultarlos para una futura interfaz; no se ha implementado la interfaz ni se
generan capturas de imagen dentro del backend. El script de smoke puede dibujar
esos cuadros en PDF temporales para revisión humana.

El comienzo de otra extracción aceptada invalida la vista anterior. Las vistas
caducan a los diez minutos; una generación anterior no puede sobrescribir una
más reciente. No se guarda la vista en SQLite ni en volúmenes. Es un mecanismo
de desarrollo para un proceso y un operador confiable, no autenticación multiusuario;
cualquier persona con el token puede ver el último documento de ese proceso.
El README documenta límites de tamaño, autenticación y riesgos de privacidad.

## Pendiente para una evaluación de calidad

- Revisar visualmente todas las apariciones y posibles falsos positivos de
  varios documentos; no tomar un aumento en el número de cuadros como prueba de
  precisión. Registrar únicamente las observaciones reales del revisor.
- Medir cobertura y precisión sobre un conjunto anotado, incluidos apellidos
  cortos, homónimos, nombres compuestos, OCR ruidoso y documentos multilingües.
- Calibrar límites y umbrales; una comparación por componentes puede confundir
  personas distintas con nombres parecidos.
- Diseñar autorización y almacenamiento compartido antes de habilitar la vista
  para varios usuarios, procesos o réplicas. El prototipo actual no pretende
  resolver ese despliegue.

La guía operativa es [GUIA_SMOKE_VISUAL.md](GUIA_SMOKE_VISUAL.md); los resultados
de ejecución se registran en [TEST_LOG.md](TEST_LOG.md).
