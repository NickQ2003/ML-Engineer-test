# Recorrido visual: OCR, ingesta y Qdrant

Esta guía permite seguir el flujo real **PDF → OCR → fragmentos → embeddings →
Qdrant + SQLite**, ver los datos en una interfaz gráfica y comprobar casos
importantes. El recorrido usa los servicios y modelos reales; el PDF con una
página vacía es un documento **sintético**.

> **Seguridad:** el recorrido crea sus propios contenedores, red y volúmenes;
> **no inicia, reinicia ni modifica el Compose existente**. Publica API y
> Qdrant solo en `127.0.0.1`, en puertos aleatorios. Su red Docker es dedicada,
> pero no es `--internal`: no se afirma que los contenedores carezcan de salida
> a Internet. El panel Qdrant no tiene autenticación y permite modificar sus
> datos: úsalo únicamente con información de prueba, no con PDF sensibles.

## 1. Preparación

Desde la raíz del repositorio, con Docker Desktop iniciado:

```bash
# La construcción del modelo puede requerir Internet y varios minutos.
docker build -t document-intelligence-smoke:local .

# El recorrido requiere esta imagen de Qdrant. Si falta, descárgala:
docker image inspect qdrant/qdrant:v1.13.6 >/dev/null || docker pull qdrant/qdrant:v1.13.6

bash scripts/smoke/recorrido_visual.sh
```

Si ya tienes otra imagen actualizada del proyecto, puedes evitar reconstruir:

```bash
SMOKE_IMAGE=document-intelligence-h4-model:local bash scripts/smoke/recorrido_visual.sh
```

El script se detiene después de cada etapa. **No cierres esa terminal** mientras
quieras inspeccionar los datos. Pulsa Enter para avanzar; `Ctrl-C` también
termina el recorrido. Al finalizar se detienen los dos contenedores propios y
se eliminan sus volúmenes y archivos temporales, incluidos los datos del panel
y la copia SQLite. Si el proceso o Docker terminan abruptamente, la limpieza
puede no ejecutarse. No borres recursos desconocidos: revisa sus nombres y
etiquetas antes de realizar cualquier limpieza manual.

## 2. Conocer los servicios de prueba

En el **paso 1**, el script imprime direcciones como estas; tus puertos serán
diferentes:

```text
API:              http://127.0.0.1:PUERTO_API/docs
Qdrant Dashboard: http://127.0.0.1:PUERTO_QDRANT/dashboard/
```

Abre ambos enlaces en el navegador. `/docs` es la documentación interactiva de
la API, no una interfaz de producto. Los recursos visuales de Swagger pueden
requerir Internet; si no cargan, el esquema sigue disponible en
`http://127.0.0.1:PUERTO_API/openapi.json` y puedes usar `curl`. La colección
todavía puede no existir: se crea durante la primera ingesta. El servicio que
ya estuviera ejecutándose en el puerto 8000 **no** es este entorno aislado.

## 3. Primer PDF: compara fuente, páginas y fragmentos

En el **paso 2** se envía `sample_pdfs/meeting_minutes.pdf` mediante HTTP real.
Abre ese PDF con Vista Previa u otro lector. La salida incluye:

- `respuesta_http.status = success` y `chunks_stored > 0`;
- `ingestion_id`, `document_id`, el SHA-256 del PDF y `estado = completed`;
- las páginas físicas con `page_number`, `text_detected` y `chunk_count`;
- el ID de un punto, sus **384 dimensiones** y el resultado de consultarlo
  contra su propio vector mediante Qdrant.

Las páginas comienzan en **0**: la página 0 corresponde a la primera página que
ves en el lector. `chunks_stored` debe coincidir con la suma de `chunk_count`.
El OCR puede tener errores; compáralo visualmente con el original.

## 4. Reingesta y página vacía

En el **paso 3** se vuelve a enviar exactamente el mismo archivo. Comprueba que
el **SHA-256 es igual**, pero `ingestion_id` y `document_id` son distintos. No
hay deduplicación automática.

En el **paso 4** se genera e ingiere un escaneo sintético de dos páginas. El
script muestra la ruta temporal del PDF exacto que acaba de enviar: ábrelo y
observa que la primera página está vacía. El resultado esperado es:

```text
página 0: text_detected=false, chunk_count=0
página 1: text_detected=true,  chunk_count>0
```

Además de revisar SQLite, el script consulta Qdrant y verifica que la página 0
no tenga puntos. El PDF temporal se elimina al terminar el recorrido.

## 5. Ver los datos en Qdrant Dashboard

En el enlace del panel, abre la colección
`documents_minilm_1110a243_v1` y busca su explorador de puntos o **Console**.
La colección contiene un punto reservado con `kind=collection_manifest`: es
metadato de propiedad, **no** un fragmento. Filtra por `kind=chunk`.

En la consola de Qdrant puedes listar fragmentos con:

```http
POST /collections/documents_minilm_1110a243_v1/points/scroll
{
  "limit": 20,
  "with_payload": true,
  "with_vector": false,
  "filter": {"must": [{"key": "kind", "match": {"value": "chunk"}}]}
}
```

Abre un `payload` y comprueba `ingestion_id`, `document_id`, `page_number`,
`text`, `char_start`, `char_end`, `model_name` y `model_revision`. El texto es
el OCR y los offsets son locales a la página; los fragmentos contiguos pueden
solaparse. El recorrido envía sus archivos con el nombre `sample.pdf`, por lo
que `source_file` en esos ejemplos no distingue los dos PDF. Activa
`with_vector: true` si quieres inspeccionar los 384 números: son componentes
del embedding, no texto legible.

Para contar una ingesta específica, sustituye `INGESTION_UUID` por el ID que
imprimió el script:

```http
POST /collections/documents_minilm_1110a243_v1/points/count
{
  "exact": true,
  "filter": {"must": [
    {"key": "kind", "match": {"value": "chunk"}},
    {"key": "ingestion_id", "match": {"value": "INGESTION_UUID"}}
  ]}
}
```

El resultado `count` debe coincidir con `fragmentos_esperados` del registro
completado. Para explorar similitud, sustituye también `PUNTO_UUID` por el
`punto_id` impreso en el paso 2:

```http
POST /collections/documents_minilm_1110a243_v1/points/query
{
  "query": "PUNTO_UUID",
  "limit": 5,
  "with_payload": true,
  "filter": {"must": [
    {"key": "kind", "match": {"value": "chunk"}},
    {"key": "ingestion_id", "match": {"value": "INGESTION_UUID"}}
  ]}
}
```

La consulta por ID muestra otros vecinos de ese vector y sus puntuaciones (puede
excluir el propio punto). El script, en cambio, consulta con el vector completo
para comprobar la similitud consigo mismo. Ninguna de las dos es una
evaluación de calidad de recuperación ni implementa `/api/ask`. En el producto
futuro también habrá que excluir las ingestas que no estén completadas.

## 6. Ver el ledger con una herramienta gráfica

En el **paso 5** el script imprime una ruta temporal a `ledger.sqlite3`. Es
una **copia coherente**, realizada con la API de copia de seguridad de SQLite;
se entrega como archivo independiente, no como copia del archivo vivo sin su
WAL. Ábrela con
[DB Browser for SQLite](https://sqlitebrowser.org/) u otro visor SQLite y
consulta las tablas `ingestions` e `ingestion_pages`.

En la pestaña de SQL puedes ejecutar:

```sql
SELECT ingestion_id, document_id, substr(content_sha256, 1, 16) AS sha256_inicio,
       state, physical_pages, expected_chunks, write_uncertain
FROM ingestions ORDER BY created_at;
```

```sql
SELECT i.ingestion_id, p.page_number, p.text_detected, p.chunk_count
FROM ingestion_pages AS p
JOIN ingestions AS i ON i.ingestion_id = p.ingestion_id
ORDER BY i.created_at, p.page_number;
```

```sql
SELECT content_sha256, COUNT(*) AS intentos
FROM ingestions GROUP BY content_sha256 HAVING COUNT(*) > 1;
```

La copia refleja el momento en que se creó, no las ingestas posteriores. SQLite
contiene identificadores, hash y estados, **no** los PDF ni el texto OCR. Cierra
el visor antes de pulsar Enter: el script elimina la copia al terminar. Si
necesitas conservarla, copia el archivo a una ruta elegida por ti antes de salir.

## 7. Probar otro PDF y errores

Antes de avanzar al paso 5, puedes probar otro PDF desde `/docs`: despliega
`POST /api/ingest`, pulsa **Try it out**, selecciona `pdf_file` y ejecuta.
Espera la respuesta; el primer uso del modelo puede tardar. La respuesta no
devuelve el identificador: encuéntralo en el panel o en la copia SQLite creada
después. Usa únicamente documentos de prueba.

Para comprobar un PDF inválido desde otra terminal, copia la URL de la API que
mostró el script y sustituye el puerto:

```bash
API_VISUAL=http://127.0.0.1:PUERTO_API
curl -sS -w '\nHTTP %{http_code}\n' "$API_VISUAL/api/ingest" \
  -F 'pdf_file=@/dev/null;filename=invalido.pdf;type=application/pdf'
# Esperado: HTTP 400
```

Un documento sin texto OCR devuelve `422`; límites excedidos devuelven `413` y
dependencias o capacidad no disponibles, `503`. Una página visualmente vacía no
es lo mismo que una página en la que el OCR no logró reconocer texto.

### Revisión visual de recuadros de extracción

Como comprobación adicional del hito anterior, en esta copia local pueden estar
disponibles `docs_dev/meeting_minutes_ocr_visual.pdf` (recuadros de palabras) y
`docs_dev/meeting_minutes_names_visual.pdf` (recuadros de nombres). En macOS
puedes abrirlos con `open RUTA_DEL_ARCHIVO`. Son evidencias locales excluidas de
Git, no parte del recorrido temporal. Compáralas con el original y revisa
omisiones y desalineaciones en ambas páginas. El procedimiento y el estado de la
revisión humana están en [MANUAL_TEST_LOG.md](MANUAL_TEST_LOG.md#mt-002--visual-placement-of-full-names);
generar la evidencia no significa que MT-002 esté aprobada. Si los archivos no
existen, consulta su procedimiento de generación en
[TEST_LOG.md](TEST_LOG.md#evidence-for-human-visual-review).

Para comparar la recuperación opcional de nombres repetidos con sus cuatro
niveles sobre HTTP, OCR y NER reales:

```bash
docker build -t document-intelligence-name-recovery:local .
SMOKE_IMAGE=document-intelligence-name-recovery:local \
  bash scripts/smoke/recuperacion_nombres.sh
```

El script imprime cuatro copias PDF temporales: abre `level-0.pdf` junto a
`level-1.pdf`, `level-2.pdf` y `level-3.pdf` para comparar los cuadros rojos.
Cada nivel usa una solicitud y un contenedor aislado con la imagen empaquetada,
sin red y sin afectar Compose. La comparación verifica que se conserven los
cuadros originales en esas ejecuciones y muestra la cantidad añadida; una persona
debe decidir si cada nueva coincidencia es correcta. Los PDF y las respuestas
temporales se eliminan al pulsar Enter o `Ctrl-C`; `--automatico` no hace pausas.
No es una evaluación de calidad ni reemplaza la revisión humana MT-002.
Las copias anteriores que se hayan guardado manualmente en `docs_dev/` no se
actualizan automáticamente; vuelve a ejecutar el smoke con la imagen actual para
comparar el código vigente.

### Vista temporal de cada etapa de extracción

El backend también dispone de una vista **opcional y desactivada por defecto**
para la futura UI. Conserva en memoria solo el último PDF extraído y resultados
inmutables con los cuadros de OCR, NER, cada pasada de recuperación y resultado
final. Una nueva extracción invalida inmediatamente la vista anterior; también
caduca a los 10 minutos. No se guardan estos snapshots en SQLite ni en volúmenes.

Para comprobarlos y abrir copias anotadas sin alterar Compose:

```bash
docker build -t document-intelligence-stage-preview:local .
SMOKE_IMAGE=document-intelligence-stage-preview:local bash scripts/smoke/vista_etapas.sh
```

Abre los archivos temporales `ocr.pdf`, `ner.pdf`, `recovery_1.pdf` y
`final.pdf` que imprime el script. Rojo representa cuadros OCR o NER y verde los
cuadros recuperados. Estos PDF los genera **el smoke test** para revisión; el
backend entrega un proxy PDF y datos JSON de cuadros, no una interfaz gráfica.
Se eliminan al terminar la prueba. `--automatico` los comprueba sin pausa.

Para usar la API fuera de la prueba hay que configurar antes
`EXTRACT_PREVIEW_ENABLED=true` y un `EXTRACT_PREVIEW_TOKEN` privado de al menos
32 caracteres. Se consulta `GET /api/extract/preview` con `Authorization: Bearer
TOKEN`, seguido de los enlaces documentados en el README. Está pensada para un
solo operador y proceso, no para uso multiusuario o múltiples réplicas: cualquier
persona con el token puede ver el documento más reciente de ese proceso. El
documento y los cuadros pueden contener información sensible; usa únicamente
un entorno confiable, TLS si corresponde y un visor PDF aislado.

## 8. Comprobaciones automáticas más profundas

El recorrido visual no sustituye las pruebas de recuperación y persistencia. En
otra ejecución, tras cerrar el recorrido visual, ejecuta:

```bash
# HTTP + OCR + embeddings + SQLite + Qdrant reales; luego recrea solo sus
# contenedores temporales, comprueba persistencia y elimina sus recursos.
SMOKE_PACKAGED_APP=1 bash scripts/smoke/ingestion.sh

# Regresión del flujo de extracción real, componentes y HTTP.
SMOKE_PACKAGED_APP=1 bash scripts/smoke/ejecutar.sh todo
```

Estas pruebas incluyen un registro parcial, una escritura deliberadamente
retrasada y fallos del manifiesto **simulados** sobre Qdrant real: no prueban un
timeout de servidor real. El borrado de una ingesta activa o completada debe
ser rechazado. SQLite y Qdrant no comparten transacción; un resultado de escritura
incierto requiere que un operador confirme la quiescencia antes de limpiar.

Para repetir además la batería de pruebas unitarias e integración en un
contenedor sin red, crea una imagen de prueba con `pytest` y monta los archivos
en solo lectura. La instalación inicial de `pytest` puede requerir Internet:

```bash
docker build -t document-intelligence-smoke-pytest:local - <<'DOCKERFILE'
FROM document-intelligence-smoke:local
RUN uv pip install --target /tmp/test-deps "pytest==9.2.0"
DOCKERFILE

docker run --rm --network none \
  -v "$PWD/app:/app/app:ro" -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e PYTHONPATH=/tmp/test-deps:/app -e SAMPLE_PDFS_DIR=/samples -e HF_HUB_OFFLINE=1 \
  --entrypoint python document-intelligence-smoke-pytest:local \
  -m pytest -p no:cacheprovider /tests/unit /tests/integration
```

Esta batería mezcla pruebas con componentes simulados y otras con OCR, modelos y
subprocesos reales; no sustituye el recorrido HTTP/Qdrant anterior. Las imágenes
locales creadas permanecen después de las pruebas.

Para ejecutar el recorrido visual sin pausas, por ejemplo en una comprobación
de terminal, usa `bash scripts/smoke/recorrido_visual.sh --automatico`; en ese
modo los enlaces y archivos desaparecen al terminar. La revisión humana
MT-002, una evaluación de calidad de búsqueda y la calibración de capacidad
siguen pendientes.
