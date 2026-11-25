# Guía técnica: tecnologías, decisiones y recorrido por funciones

Esta guía explica **qué tecnología hace cada trabajo, dónde se usa, qué decisión
motivó su configuración y cuál es su alcance o límite**. El catálogo cubre el
código de aplicación (`app/`), la interfaz (`frontend/`) y el mantenimiento. Los
tests y documentos de evidencia se enlazan como material de verificación; no se
enumeran aquí las funciones internas de cada test.

> **Cómo leer los enlaces:** cada nombre enlaza al archivo fuente concreto. Los
> nombres de funciones indican qué buscar dentro del archivo. Las rutas son
> relativas a `docs/` y abren directamente en el código del repositorio.

## 1. Recorrido recomendado

1. [Diseño general](../DESIGN.md), [decisiones de ingeniería](../DECISIONS.md) y
   [contrato HTTP](../app/models/schemas.py).
2. [Arranque y ciclo de vida](../app/main.py) → [dependencias compartidas](../app/api/dependencies.py).
3. Elige un flujo: [extracción](#5-flujo-de-extraccion),
   [ingestión](#6-flujo-de-ingestion-y-embeddings) o [preguntas RAG](#7-flujo-de-preguntas-rag).
4. Revisa [configuración](#4-configuracion-y-alcance) y
   [limitaciones conocidas](#10-limites-que-no-se-deben-sobreprometer).
5. Contrasta el código con [pruebas registradas](TEST_LOG.md),
   [evaluación RAG](EVALUACION_FUENTES_RAG.md) y
   [revisión visual humana](MANUAL_TEST_LOG.md).

## 2. Mapa de arquitectura y responsabilidades

```text
HTTP /api/extract ─► parser multipart ─► OCR ─► spaCy NER ─► ubicación PDF
                                                        └─► recuperación opcional
                           └──────────────────────────────► fuzzy matching

HTTP /api/ingest ─► OCR ─► chunks por página ─► MiniLM ─► Qdrant
                              │                     │          │
                              └──── metadatos ───────┴──────► SQLite ledger

HTTP /api/ask ─► MiniLM (pregunta) ─► Qdrant + ledger ─► proveedor LLM opt-in
                         └────────────────────── citas verificadas ◄─────────┘

React + PDF.js ─► Nginx ─► FastAPI; la interfaz renderiza, no hace OCR ni NER.
```

| Capa | Responsabilidad y frontera | Código |
|---|---|---|
| HTTP y ciclo de vida | Validar forma de entrada, traducir errores a HTTP y crear/cerrar servicios por proceso. | [`main.py`](../app/main.py), [`api/`](../app/api/) |
| Modelos internos | Transportar OCR, offsets y spans entre componentes sin acoplarlos al JSON público. | [`models/document.py`](../app/models/document.py), [`models/extraction.py`](../app/models/extraction.py) |
| OCR / NER | Ejecutar modelos pesados fuera del event loop y devolver texto/offsets por página. | [`ocr_service.py`](../app/services/ocr_service.py), [`ner_async.py`](../app/services/ner_async.py) |
| Lógica de dominio | Asociar texto con geometría, recuperar nombres opcionalmente y comparar nombres completos. | [`extraction_service.py`](../app/services/extraction_service.py), [`bbox_service.py`](../app/services/bbox_service.py), [`fuzzy_service.py`](../app/services/fuzzy_service.py) |
| Ingestión | Orquestar OCR → fragmentos → vectores → persistencia y seguimiento de estados. | [`ingestion_service.py`](../app/services/ingestion_service.py) |
| Recuperación RAG | Buscar candidatos semánticos, validar que pertenecen a ingestas completas y construir citas. | [`ask_retrieval.py`](../app/services/ask_retrieval.py), [`ask_service.py`](../app/services/ask_service.py) |
| Persistencia | Qdrant contiene puntos/vector/texto; SQLite contiene hashes, estado y conteos, no el PDF ni el OCR. | [`ingestion_vectors.py`](../app/services/ingestion_vectors.py), [`ingestion_ledger.py`](../app/services/ingestion_ledger.py) |

## 3. Tecnologías y por qué se distinguen

Las versiones Python exactas quedan resueltas en [`uv.lock`](../uv.lock); las
dependencias directas están en [`pyproject.toml`](../pyproject.toml). Las
versiones del frontend están en [`frontend/package.json`](../frontend/package.json).

| Tecnología | Uso exacto | Decisión y límite |
|---|---|---|
| **Python 3.12 + uv** | Backend y workers; `uv sync --locked` construye la imagen. | `uv.lock` hace reproducible la resolución; el runtime requiere POSIX para supervisar grupos de procesos. |
| **FastAPI + Starlette + Uvicorn** | Endpoints async, middleware ASGI, parseo multipart y servidor HTTP. | FastAPI organiza rutas; Starlette expone los detalles ASGI/parser; Uvicorn sirve la app. No son el OCR ni el servidor frontend. |
| **Pydantic / pydantic-settings** | Contratos HTTP y configuración tipada desde entorno. | Valida estructura y límites antes del dominio; los modelos públicos se preservan en `schemas.py`. `SecretStr` protege la representación de claves, no al administrador del host. |
| **PyMuPDF (`pymupdf`)** | Abre el PDF, renderiza páginas y define transformaciones/coordenadas. | Coordenadas devueltas en puntos PDF, relativas al crop box sin rotar; no son píxeles ni coordenadas CSS. |
| **Pillow** | Convierte el pixmap de PyMuPDF a imagen RGB para Tesseract. | Es puente de imagen en memoria, no motor OCR. |
| **Tesseract + pytesseract** | Tesseract es el ejecutable OCR; `pytesseract` lo invoca y devuelve palabras, cajas y confianza. | Imagen instala los datos ingleses (`eng`); la calidad multilingüe no está establecida. `pytesseract` no es el modelo. |
| **spaCy + `en_core_web_sm`** | NER por página; se conservan solo entidades `PERSON` y offsets. | Modelo pequeño en inglés, instalado en build y sin descarga en inferencia. La etiqueta no garantiza que sea persona ni que detecte a todos. |
| **thefuzz + python-Levenshtein** | Similaridad de nombre completo luego de normalizar acentos/case/puntuación. | Umbral predeterminado `0.90`; no es NER, embedding ni comparación semántica. La normalización puede hacer colisionar nombres distintos. |
| **sentence-transformers + PyTorch CPU** | `all-MiniLM-L6-v2` genera vectores locales de 384 dimensiones para chunks y preguntas. | Revisión fija y artefactos verificados por SHA-256; CPU reduce dependencias de CUDA. Reproducibilidad no demuestra que sea el mejor modelo para el corpus. |
| **Qdrant + qdrant-client** | Índice vectorial, similitud cosine, payload de cada fragmento y búsqueda top-k. | Servicio separado con volumen. La colección lleva un manifiesto de modelo/ledger. No es la base de estados transaccionales. |
| **SQLite** | Ledger durable local de ingestiones, páginas, estados, identidad y hashes. | No almacena texto OCR ni vectores. SQLite y Qdrant no comparten transacción; locks `fcntl` solo coordinan procesos cooperantes del mismo host. |
| **httpx** | Cliente async para Gemini/OpenAI-compatible. | Provider desactivado por defecto; exige configuración explícita para enviar pregunta y fragmentos fuera del host. Retries selectivos; no se repiten 429 ni timeouts de lectura/escritura. |
| **AnyIO + asyncio + subprocesses POSIX** | Semáforos, deadlines, cancelación y workers aislados para OCR/NER/embeddings. | Evita bloquear el event loop y supervisa hijos. Los límites son por proceso, no globales; una caída abrupta del host puede impedir limpieza. |
| **React + PDF.js + Vite** | Formulario y renderizado local del PDF con overlays. | PDF.js muestra el PDF; el backend hace OCR/NER. Vite empaqueta los assets y el worker `.mjs`. |
| **Nginx** | Sirve frontend estático y proxya `/api/` al backend. | Bindea frontend a loopback en Compose y añade límites/cabeceras; el token interno del preview no es autenticación de usuarios para toda la API. |
| **Docker Compose** | Ejecuta app, Qdrant, frontend y generación del secreto de preview. | Facilita el entorno local; no implementa alta disponibilidad ni aislamiento multi-tenant. El Dockerfile ejecuta la app como `appuser`. |
| **pytest / Node test runner** | Tests backend y frontend; smoke/E2E aislados en scripts. | Separar unitarias simuladas, integración con dependencias reales y llamadas reales al proveedor. Un test verde no equivale a métrica de calidad general. |

### Versiones que están fijadas hoy

`uv.lock` es la autoridad para paquetes Python; `package-lock.json` lo es para
NPM. Estos números ayudan a reproducir la presentación; si cambia el lock, vuelve
a prevalecer el archivo de lock.

| Componente | Versión fijada |
|---|---|
| Python | `>=3.12`; imagen `python:3.12-slim-bookworm` |
| uv | `0.13.8` en la etapa de build |
| FastAPI / Starlette / Uvicorn | `0.163.0` / `1.10.0` / `0.54.0` |
| Pydantic / pydantic-settings | `2.14.3` / `2.16.3` |
| AnyIO / HTTPX / python-multipart | `4.16.0` / `0.28.1` / `0.0.33` |
| PyMuPDF / Pillow / pytesseract | `2.1` / `13.0.0` / `0.3.13` |
| spaCy / `en_core_web_sm` | `3.8.18` / `3.8.0` |
| thefuzz / python-Levenshtein | `0.22.1` / `0.27.5` |
| sentence-transformers | `6.3.0` |
| PyTorch | `2.15.0+cpu` Linux; `2.15.0` otras plataformas |
| qdrant-client / servidor Qdrant | `1.20.0` / `qdrant/qdrant:v1.13.6` |
| pytest / pytest-asyncio | `9.2.0` / `1.5.0` |
| React / React DOM | `19.3.1` / `19.3.1` |
| PDF.js / Vite | `6.5.438` / `8.3.8` |
| Frontend container | build `node:22-alpine`; runtime `nginxinc/nginx-unprivileged:1.29-alpine` |

Tesseract **no** queda fijado por `uv.lock`: es un paquete del sistema instalado
por `apt` en el Dockerfile. `pytesseract` es solo la dependencia Python que lo
invoca. Para una reproducción bit-a-bit también habría que fijar base image por
digest y repositorio/versiones de paquetes del sistema.

### Tecnologías que parecen similares, pero cumplen trabajos distintos

- **OCR vs NER:** OCR reconoce caracteres y ubicaciones; NER decide qué spans
  parecen nombres de persona. Un fallo de OCR puede propagarse al NER.
- **NER vs fuzzy matching vs recuperación:** NER propone personas; fuzzy compara
  cada nombre detectado con la consulta del usuario; recuperación intenta hallar
  más ocurrencias basándose en spans NER ya ubicados. No reemplaza un modelo NER.
- **Embedding vs Qdrant:** Sentence Transformers convierte texto en números;
  Qdrant indexa/busca esos números y devuelve payloads. El embedding no responde
  preguntas ni prueba que dos textos sean equivalentes.
- **Qdrant vs SQLite:** Qdrant optimiza similitud; SQLite decide si un intento
  está completo y registra su procedencia. La lectura RAG cruza ambos.
- **Fuente vs cita:** el source indica documento/página; el quote interno valida
  que la subcadena existe en un fragmento. Una subcadena literal no prueba que
  esa evidencia implique semánticamente la respuesta.

## 4. Configuración y alcance

La fuente tipada es [`Settings`](../app/core/config.py); los valores de ejemplo
para Compose están en [`.env.example`](../.env.example) y su cableado en
[`docker-compose.yml`](../docker-compose.yml). **El valor efectivo puede diferir**
del default Python: por ejemplo, `NER_NAME_RECOVERY_PASSES` vale `0` en `Settings`
y en el fallback de Compose, pero `.env.example` lo configura en `1`.

| Grupo | Defaults importantes | Qué gobierna |
|---|---|---|
| Aplicación | `APP_ENV=development`, `LOG_LEVEL=INFO` | Entorno declarativo y nivel de registros JSON. |
| Qdrant | `QDRANT_URL=http://qdrant:6333`; colección de ingestión `documents_minilm_1110a243_v1`; timeout `10s`; dos retries de lectura; batch `64`. | El nombre de ingestión separa el índice para la revisión/dimensiones fijadas. `QDRANT_COLLECTION=documents` queda para la implementación antigua de scaffold, no para la ruta activa. |
| Subida/extracción | PDF `20 MiB`; cuerpo multipart `21 MiB`; `names` `64 KiB`, hasta `1000` parejas; componente hasta `512` chars; threshold `0.90`; dos requests simultáneos por proceso; lectura `30s`. | Distingue bytes del PDF y bytes del request completo, que incluyen multipart. Hay límites extra de spans/comparaciones para fuzzy. |
| OCR | `100` páginas; `20,000,000` píxeles por página; `200 DPI`; `eng`; tarea OCR `60s`; 2 activos + 2 en cola; espera `10s`; documento `180s`; orientación alternativa si confianza `<80`. | Controla renderizado, idioma y admisión. No garantiza corrección de rotación/skew. |
| NER | `en_core_web_sm`; página `100,000` chars; documento `1,000,000`; 1 worker + 1 en cola; cola `10s`; job `60s`; shutdown `10s`. | Spans por página; valores de concurrencia son por worker del servidor. Recuperación `0..3`; `0` desactiva. |
| Embedding/ingesta | Modelo `/opt/models/all-minilm-l6-v2`; batch `16`; worker `120s`; hasta `100` páginas; chunk `128` tokens; overlap hasta `32`; `1000` chunks; página `100,000` chars; documento `1,000,000`; un ingest simultáneo. | `128` cuenta tokens especiales; chunker conserva palabras y páginas. Es baseline, no optimum medido. Query admite hasta `256` tokens. |
| LEDGER | `/app/data/ingestion/ledger.sqlite3`; volumen `ingestion_data`. | Mantiene historial/recuperación en el host. No compartir sobre NFS ni hosts distintos. |
| Ask/RAG | `100` candidatos; body `16 KiB`; 2 concurrentes por proceso; upload `10s`; job `120s`; máximo 5 fragmentos en contexto. | El límite de candidatos es distinto del máximo de fragmentos entregados al generador. |
| LLM | `LLM_PROVIDER=disabled`; external consent `false`; timeout `45s`; salida `512` tokens; 1 retry con backoff `0.25s`. | Gemini/OpenAI requiere provider, modelo, key y `LLM_ALLOW_EXTERNAL=true`. OpenAI base URL debe ser HTTPS. |
| Preview | Python default deshabilitado; Compose lo habilita por default, TTL `600s`; preview máx PDF `10 MiB`, `25` páginas y límites de bytes de stages. | Retiene solo el último preview en memoria del proceso. Secret interno autoriza solo estas rutas; el preview no convierte el API en autenticado. |

### Defaults exactos de `Settings`

Los nombres de la tabla son los nombres de entorno en mayúsculas; Pydantic los
convierte a los campos de [`Settings`](../app/core/config.py). Cuando se indica
“Compose/.env”, el valor de `docker-compose.yml` o `.env` puede reemplazar el
default Python.

| Variable | Default Python | Alcance / decisión |
|---|---:|---|
| `APP_NAME` | `Document Intelligence Service` | Título FastAPI. |
| `APP_ENV` | `development` | Etiqueta de entorno; no activa por sí sola controles de producción. |
| `LOG_LEVEL` | `INFO` | Se aplica a app y Uvicorn. |
| `QDRANT_URL` | `http://qdrant:6333` | Endpoint del servicio vectorial. |
| `QDRANT_COLLECTION` | `documents` | Campo heredado del scaffold; la ruta de ingestión usa `INGEST_COLLECTION`. |
| `MAX_UPLOAD_BYTES` | `20 MiB` (`20971520`) | Límite del contenido PDF. |
| `MAX_REQUEST_BYTES` | `21 MiB` (`22020096` en Compose) | Límite del body completo de `/api/extract` y `/api/ingest`, incluido multipart. |
| `MAX_NAMES_BYTES` | `64 KiB` (`65536`) | Tamaño UTF-8 del campo `names` ya decodificado. |
| `MAX_NAMES_COUNT` | `1000` | Máximo de parejas; se permite lista vacía. |
| `MAX_NAME_CHARS` | `512` | Caracteres por nombre de consulta y entidad. |
| `FUZZY_THRESHOLD` | `0.90` | Umbral sobre ratio full-string normalizado. |
| `EXTRACT_MAX_IN_FLIGHT` | `2` | Solicitudes de extracción activas por proceso. |
| `EXTRACT_UPLOAD_TIMEOUT_SECONDS` | `30` | Deadline de lectura del request de extracción. |
| `EXTRACT_PREVIEW_ENABLED` | `false` en Python; `true` en Compose | Habilita stages visuales opcionales. |
| `EXTRACT_PREVIEW_TOKEN` | Sin token | Si preview está activo exige 32–256 caracteres ASCII imprimibles no espacio; Compose genera el secreto interno. |
| `MAX_EXTRACTED_NAMES` | `1000` | Tope de entidades NER antes de asociación. |
| `MAX_FUZZY_COMPARISONS` | `100000` | Tope de pares nombre detectado/consulta. |
| `MAX_FUZZY_WORK_UNITS` | `100000000` | Tope de producto de longitudes normalizadas. |
| `NER_MODEL` | `en_core_web_sm` | Modelo spaCy instalado; no descarga/fallback en runtime. |
| `NER_NAME_RECOVERY_PASSES` | `0` | `0` off, `1` exacto, `2` fuzzy conservador, `3` recuperación de segmentación OCR; `.env.example` lo configura en `1`. |
| `NER_MAX_PAGE_CHARS` | `100000` | Límite por página; máximo validado `1000000`. |
| `NER_MAX_DOCUMENT_CHARS` | `1000000` | Suma de texto enviado a NER. |
| `NER_MAX_CONCURRENT_JOBS` | `1` | Workers spaCy activos por proceso. |
| `NER_MAX_QUEUED_JOBS` | `1` | Jobs adicionales admitidos en cola por proceso. |
| `NER_QUEUE_TIMEOUT_SECONDS` | `10` | Tiempo máximo esperando worker. |
| `NER_JOB_TIMEOUT_SECONDS` | `60` | Deadline de carga/inferencia de un job. |
| `NER_SHUTDOWN_TIMEOUT_SECONDS` | `10` | Grace period antes de terminar workers. |
| `MAX_PDF_PAGES` | `100` | Límite físico de páginas procesadas. |
| `MAX_PAGE_PIXELS` | `20000000` | Rechaza páginas rasterizadas que excedan el presupuesto. |
| `OCR_DPI` | `200` | Resolución de renderizado PDF→imagen; válido `72..600`. |
| `OCR_LANGUAGES` | `eng` | Idiomas Tesseract instalados/configurados. |
| `OCR_TIMEOUT_SECONDS` | `60` | Timeout por invocación de Tesseract. |
| `OCR_MAX_CONCURRENT_JOBS` | `2` | Procesos OCR activos por proceso API. |
| `OCR_MAX_QUEUED_JOBS` | `2` | Trabajos OCR esperando por proceso API. |
| `OCR_QUEUE_TIMEOUT_SECONDS` | `10` | Espera máxima para iniciar OCR. |
| `OCR_UPLOAD_TIMEOUT_SECONDS` | `30` | Lectura de stream usada por OCR y preview. |
| `OCR_DOCUMENT_TIMEOUT_SECONDS` | `180` | Deadline total de documento OCR. |
| `OCR_SHUTDOWN_TIMEOUT_SECONDS` | `10` | Tiempo de cierre supervisado de OCR. |
| `OCR_ORIENTATION_MIN_CONFIDENCE` | `80` | Prueba orientación alternativa si confianza menor; heurística. |
| `EMBEDDING_MODEL_DIR` | `/opt/models/all-minilm-l6-v2` | Artefactos provisionados en build y cargados offline. |
| `EMBEDDING_QUERY_MAX_TOKENS` | `256` | Máximo del query vector, incluido presupuesto del modelo. |
| `EMBEDDING_BATCH_SIZE` | `16` | Batch Sentence Transformers; rango validado `1..128`. |
| `EMBEDDING_JOB_TIMEOUT_SECONDS` | `120` | Deadline del worker embeddings. |
| `INGESTION_LEDGER_PATH` | `/app/data/ingestion/ledger.sqlite3` | Ruta bajo volumen privado de Compose. |
| `INGEST_COLLECTION` | `documents_minilm_1110a243_v1` | Colección aislada para identidad/revisión del embedding. |
| `INGEST_MAX_IN_FLIGHT` | `1` | Ingestas simultáneas por proceso. |
| `INGEST_UPLOAD_TIMEOUT_SECONDS` | `30` | Deadline de lectura PDF ingestión. |
| `INGEST_JOB_TIMEOUT_SECONDS` | `420` | Deadline total de OCR→persistencia. |
| `INGEST_MAX_CHUNKS` | `1000` | Tope de fragments por PDF. |
| `INGEST_MAX_PAGE_CHARS` | `100000` | Chars OCR por página antes de chunking. |
| `INGEST_MAX_DOCUMENT_CHARS` | `1000000` | Chars OCR sumados por documento. |
| `INGEST_CHUNK_TOKENS` | `128` | Tope chunk, tokens especiales incluidos; máximo `256`. |
| `INGEST_OVERLAP_TOKENS` | `32` | Overlap máximo; se reduce en bordes/palabras. |
| `QDRANT_TIMEOUT_SECONDS` | `10` | Timeout por operación Qdrant. |
| `QDRANT_READ_RETRIES` | `2` | Reintentos acotados solo para lecturas transitorias. |
| `QDRANT_BATCH_SIZE` | `64` | Points por upsert/verify batch. |
| `ASK_MAX_CANDIDATES` | `100` | Límite superior búsqueda Qdrant; `1..1000`. |
| `ASK_MAX_REQUEST_BYTES` | `16384` | Body máximo de pregunta JSON. |
| `ASK_MAX_IN_FLIGHT` | `2` | Requests ask activos por proceso. |
| `ASK_UPLOAD_TIMEOUT_SECONDS` | `10` | Timeout de lectura del body de pregunta. |
| `ASK_JOB_TIMEOUT_SECONDS` | `120` | Deadline embedding→retrieval→LLM. |
| `LLM_PROVIDER` | `disabled` | Alternativas `gemini`, `openai`; no hay fallback automático. |
| `LLM_MODEL` | Vacío | Obligatorio con provider habilitado. |
| `LLM_API_KEY` | Sin clave | `SecretStr`; no poner en imagen ni logs. |
| `LLM_ALLOW_EXTERNAL` | `false` | Consentimiento explícito para enviar pregunta/fragmentos fuera del host. |
| `LLM_OPENAI_BASE_URL` | `https://api.openai.com/v1` | Solo HTTPS, sin credenciales/query/fragment. |
| `LLM_TIMEOUT_SECONDS` | `45` | Timeout HTTP del proveedor; máximo `300`. |
| `LLM_MAX_OUTPUT_TOKENS` | `512` | Presupuesto de salida; rango `32..2048`. |
| `LLM_MAX_RETRIES` | `1` | Retries `0..3`, solo conexión/ciertos 5xx. |
| `LLM_RETRY_BACKOFF_SECONDS` | `0.25` | Backoff exponencial, máximo `5s`. |
| Constantes preview | TTL `600s`; PDF `10 MiB`; `25` páginas; `5000` palabras OCR; `2 MiB` por etapa y `8 MiB` total. | Límites internos de [`extraction_preview.py`](../app/services/extraction_preview.py); no son variables de entorno. |

Compose además define `APP_PORT=8000` y `FRONTEND_PORT=3000`; el puerto frontend
se publica solo en loopback. Los valores y comentarios duplicados para edición
local viven en [`.env.example`](../.env.example).

### Build y servicios

- [`Dockerfile`](../Dockerfile): Python `3.12-slim-bookworm`; instala
  `tesseract-ocr` y `tesseract-ocr-eng`; fija dependencias con `uv --locked`;
  comprueba spaCy NER y provisiona el embedding durante la construcción; runtime
  no descarga el modelo. La app corre como usuario sin privilegios.
- [`docker-compose.yml`](../docker-compose.yml): `app`, `qdrant`, `frontend` y
  `preview-secret`. Volúmenes separados para Qdrant, ledger y secreto. El frontend
  se expone solo en `127.0.0.1`; el backend host port se configura con `APP_PORT`.
- [`frontend/nginx.conf`](../frontend/nginx.conf): límite de body `21m`, CSP,
  proxy para API y MIME para worker PDF.js `.mjs`.
- [`Makefile`](../Makefile): atajos `quickstart`, `setup`, `up`, tests y smoke.
  `make quickstart` requiere consentimiento y configuración de Gemini; no es
  necesario usar un LLM para extracción ni ingestión.

## 5. Flujo de extracción

### Secuencia real

`POST /api/extract` → middleware de bytes/admisión → multipart controlado → JSON
`names` validado → OCR de todas las páginas → NER page-local → spans asociados a
palabras OCR del mismo pase → recuperación opcional → fuzzy match → schema de
respuesta. Las páginas de extracción son **zero-based** y los cuadros están en
puntos PDF; las fuentes RAG, en cambio, se presentan **one-based**.

| Paso / función | Alcance | Código |
|---|---|---|
| Vida de app: `lifespan`, `health` | Una instancia de OCR, NER, ingestión y retrieval por proceso; cierra recursos. `/health` solo comprueba vida de API. | [`app/main.py`](../app/main.py) |
| Dependencias: `get_ocr_service`, `get_ner_service`, `get_extraction_service`, `get_ingestion_service`, `get_retrieval_service`, `get_ask_service` | Recuperan instancias del estado de FastAPI y construyen el orquestador de extracción/Ask con dependencias explícitas. | [`app/api/dependencies.py`](../app/api/dependencies.py) |
| `ExtractionBodyLimitMiddleware.__call__` | Antes del parser cuenta bytes realmente consumidos, limita concurrencia y deadline; `Content-Length` es solo rechazo temprano, no conteo confiable. | [`app/core/request_limits.py`](../app/core/request_limits.py) |
| `parse_extraction_form`, `_ExtractionMultipartParser.on_part_data`, `close_extraction_form` | Limita PDF y campos durante parseo; exige boundary final y cierra temporales aun con cancelación. | [`app/api/extraction_form.py`](../app/api/extraction_form.py) |
| `parse_names`, `_reject_json_constant` | Acepta solo una lista JSON válida de `NamePair`; preserva spelling; rechaza constantes no JSON, strings vacíos, Unicode inválido y exceso de bytes/items. | [`app/services/extraction_input.py`](../app/services/extraction_input.py) |
| Endpoint `extract` | Adapta HTTP: parsea input, delega al servicio, cierra el form y convierte errores de dominio a códigos HTTP sin reflejar texto sensible. | [`app/api/extract.py`](../app/api/extract.py) |
| `OCRService.extract_stream_async`, `_schedule`, `_run_admitted`, `_execute_job` | Reserva capacidad antes de leer, ejecuta worker POSIX y supervisa timeout/cancelación. | [`app/services/ocr_service.py`](../app/services/ocr_service.py) |
| `OCRService._extract_page`, `_word_box`, `_mean_confidence` | Renderiza cada página, llama Tesseract `image_to_data`, reconstruye texto/offsets y transforma boxes de pixeles/rotación a crop-relative PDF points. | [`app/services/ocr_service.py`](../app/services/ocr_service.py) |
| `ocr_worker.main`, `_document_from_dict` | Worker procesa un PDF en directorio temporal y devuelve datos serializados; el padre valida/rehidrata contratos. | [`ocr_worker.py`](../app/services/ocr_worker.py), [`document.py`](../app/models/document.py) |
| `NERService.detect_pages`, `_WorkerSlot.detect`, `SpacyPersonRecognizer.detect_page` | Carga spaCy una vez por worker, procesa cada página por separado, devuelve offsets character `[start,end)` para `PERSON`. | [`ner_async.py`](../app/services/ner_async.py), [`ner_service.py`](../app/services/ner_service.py), [`ner_worker.py`](../app/services/ner_worker.py) |
| `ExtractionService.extract`, `_assemble` | Ejecuta OCR una vez, NER después y assembly CPU en thread; mantiene el orden, omite spans sin ubicación segura, opcionalmente recupera nombres y luego compara. | [`extraction_service.py`](../app/services/extraction_service.py) |
| `PageLocator.locate`, `_valid_word`, `_adjacent`, `_locate` | Comprueba offsets/texto/geometría y proximidad en mismo bloque/línea; une cajas solo si no hay gaps ni saltos dudosos. | [`bbox_service.py`](../app/services/bbox_service.py) |
| `recover_names`, `_fuzzy_match`, `_at_most_one_edit`, `_Budget`, `_Intervals` | Pasadas acotadas buscan repeticiones similares a spans NER ya ubicados; ambiguos quedan bloqueados, los recuperados no se convierten en semillas nuevas. | [`name_recovery.py`](../app/services/name_recovery.py) |
| `normalize_name`, `match_names` | Normaliza para comparar nombres completos con `thefuzz.ratio`; no modifica el nombre original devuelto. | [`fuzzy_service.py`](../app/services/fuzzy_service.py) |
| `LatestExtractionPreview` y endpoints de preview | Mantienen temporalmente PDF/stages del último resultado en memoria, exigen bearer token y headers no-cache. Opcional; no cambia la respuesta final. | [`extraction_preview.py`](../app/services/extraction_preview.py), [`api/extraction_preview.py`](../app/api/extraction_preview.py) |

### Contratos internos clave

- [`OCRDocument`, `OCRPage`, `OCRWord`, `WordBox`](../app/models/document.py):
  geometría y offsets OCR; cada offset se interpreta dentro de una página.
- [`PersonSpan`](../app/models/extraction.py): nombre NER y rango `[start,end)`;
  el consumidor todavía valida que apunte al mismo texto OCR.
- [`ExtractionResponse`, `BoundingBox`, `FuzzyMatch`](../app/models/schemas.py):
  contrato público que no debe confundirse con estructuras internas.
- [`find_name_bounding_boxes`](../app/services/bbox_service.py) es un helper
  síncrono de compatibilidad que vuelve a hacer OCR. **El endpoint HTTP usa
  `PageLocator` con el OCR ya calculado** y no debe llamarlo.

## 6. Flujo de ingestión y embeddings

### Secuencia real

`POST /api/ingest` → body/upload validation → hash + nuevo `ingestion_id` y
`document_id` → estado `processing` en SQLite → OCR → chunk por página → embedding
de cada chunk → estado `writing` → upsert por lotes en Qdrant → verificación de
IDs/conteo → estado `completed`. Cada subida, incluso un PDF repetido, crea una
ingesta nueva; el SHA-256 permite identificar bytes, no auto-deduplicar.

| Función | Alcance | Código |
|---|---|---|
| Endpoint `ingest` | Acepta un único PDF multipart y traduce errores de OCR/chunk/embedding/ledger/Qdrant a HTTP. | [`app/api/rag.py`](../app/api/rag.py) |
| `IngestionService.ingest`, `_run_with_deadline`, `_run`, `aclose` | Reserva capacidad; lee con deadline y límite de bytes; ejecuta OCR, embedding y persistencia; espera limpieza si se cancela el caller. | [`ingestion_service.py`](../app/services/ingestion_service.py) |
| `processing_signature`, `safe_filename`, `error_code` | Hash de la receta de procesamiento; sanitiza nombre solo para metadata; convierte excepciones a códigos no sensibles para el ledger. | [`ingestion_service.py`](../app/services/ingestion_service.py) |
| `TokenAwareChunker.split_page`, `_count` | Agrupa palabras completas con tokenizer real, tokens especiales incluidos, preserva índices/offsets por página y overlap limitado. | [`chunking_service.py`](../app/services/chunking_service.py) |
| `EmbeddingService.prepare`, `embed_query`, `_run` | Protocolo JSON-lines con worker persistente; usa el mismo modelo para documentos y preguntas; valida dimensión, finitud y offsets de salida. | [`embedding_async.py`](../app/services/embedding_async.py) |
| `EmbeddingModel.encode` | Sin truncar silenciosamente: tokeniza sin truncation, limita entradas y batch, crea vectores normalizados CPU de 384 floats. | [`embedding_service.py`](../app/services/embedding_service.py) |
| `EmbeddingModel.verify_model_files`, `verified_model_snapshot`, `provision_model` | Build descarga una revisión fija; verifica los 10 archivos allowlisted y carga solo una snapshot privada local. | [`embedding_model.py`](../app/core/embedding_model.py) |
| `embedding_worker.prepare`, `process`, `send`, `main` | Recibe páginas/config, chunking, encode; también acepta operación `query`; mantiene modelo cargado mientras reutiliza proceso. | [`embedding_worker.py`](../app/services/embedding_worker.py) |
| `IngestionLedger.create`, `record_pages`, `set_chunk_counts`, `start_writing`, `write_intent`, `write_confirmed`, `complete`, `fail`, `recover`, `mark_cleaned` | Controla transiciones y cuenta páginas incluso sin texto; incertidumbre de write no se representa como rollback. | [`ingestion_ledger.py`](../app/services/ingestion_ledger.py) |
| `IngestionVectorStore.ensure`, `upsert`, `verify`, `clear_points` | Asegura tamaño 384/Cosine y manifiesto compatible; usa IDs UUIDv5 por ingesta/chunk, verifica writes y limpia por filtro exacto. | [`ingestion_vectors.py`](../app/services/ingestion_vectors.py) |

### Separación de almacenamiento

| Dato | Destino | Motivo |
|---|---|---|
| PDF original de ingestión | Memoria/archivo temporal durante procesamiento; no queda archivado por el ledger. | No diseñar persistencia documental sin retención/privacidad decididas. |
| Texto OCR del fragmento + source/page/offsets | Payload de cada punto Qdrant. | El retrieval necesita devolver fragmentos auditables. |
| Vector `384` dimensiones | Vector del punto Qdrant. | Búsqueda semántica cosine. |
| Hash del PDF, modelo/revisión, firma, estado, páginas y conteo | SQLite `ingestions` / `ingestion_pages`. | Idempotencia/inspección/eligibilidad/recuperación, sin duplicar texto OCR. |
| API response | `{"status":"success","chunks_stored":N}` | No expone vectores ni el ledger al cliente. |

Estados principales del ledger: `processing → writing → completed`; un fallo
antes de escribir puede quedar `failed`; una escritura remota con resultado
incierto pasa a `cleanup_pending`. Por eso no existe una transacción atómica
SQLite+Qdrant, y no se debe reintentar una mutación remota cuyo resultado se
desconoce sin reconciliarla.

## 7. Flujo de preguntas RAG

1. `normalize_question` elimina whitespace exterior y rechaza vacío/exceso.
2. `EmbeddingService.embed_query` calcula vector con el **mismo modelo y revisión**
   de ingestión.
3. `LedgerRetriever.retrieve` busca candidatos Qdrant y valida shape, IDs,
   colección, página, modelo, firma y estado `completed` con SQLite.
4. `AskService.ask` limita fragmentos/contexto y asigna IDs de cita `S1…`.
5. `HTTPAnswerGenerator.generate` llama al provider explícito y parsea JSON
   `answer` + `citations`.
6. El servicio valida que el ID existe y que la quote sea substring literal del
   fragmento; si no hay contexto o citas, responde abstención.

| Función | Alcance | Código |
|---|---|---|
| Endpoint `ask` | Valida JSON manualmente para no reflejar inputs inválidos; timeout y traducción de errores seguros. | [`app/api/rag.py`](../app/api/rag.py) |
| `normalize_question` | Normaliza espacios exteriores, no reescribe semánticamente ni trunca. | [`ask_input.py`](../app/services/ask_input.py) |
| `AskService.ask` | Orquesta embedding → retrieval → contexto acotado → generación → verificación estructural de citas. | [`ask_service.py`](../app/services/ask_service.py) |
| `ManagedRetrievalService.retrieve`, `_retrieve`, `aclose` | Gestiona client Qdrant por request y cancelación; no crea ledger/collection durante una pregunta. | [`ask_retrieval.py`](../app/services/ask_retrieval.py) |
| `LedgerRetriever.retrieve` | Selecciona solo puntos que pasan validación del ledger; no confunde “servicio/ledger falló” con “no hay evidencia”. | [`ask_retrieval.py`](../app/services/ask_retrieval.py) |
| `HTTPAnswerGenerator._request` | Limita bytes y retries de red/status transitorios; nunca registra body de proveedor/documentos. | [`answer_generator.py`](../app/services/answer_generator.py) |
| `HTTPAnswerGenerator.generate`, `_gemini_content` | Adapta formato Gemini u OpenAI-compatible a `GeneratedAnswer`; excluye partes internas `thought` de Gemini. | [`answer_generator.py`](../app/services/answer_generator.py) |

**Importante:** citas exactas limitan texto inventado y fuentes fuera de contexto,
pero no detectan contradicción/entailment. El prompt reduce riesgos de prompt
injection; no es una garantía. La respuesta de usuario no incluye quote literal,
solo el answer y labels de fuente/página.

## 8. Frontend, proxy y herramientas

| Función / componente | Alcance | Código |
|---|---|---|
| `main.jsx` | Monta la aplicación React. | [`frontend/src/main.jsx`](../frontend/src/main.jsx) |
| `App` | Estado de archivo, solicitud, navegación de páginas, resultado y etapa de preview; cancela operaciones obsoletas. | [`frontend/src/App.jsx`](../frontend/src/App.jsx) |
| `parseNames`, `errorMessage`, `disposePdf` | Valida input de interfaz, traduce status a UX y libera tareas PDF.js. | [`frontend/src/App.jsx`](../frontend/src/App.jsx) |
| `sameResponse`, `checkPreview` | Comprueba que el preview retornado corresponde a los mismos bytes del PDF local y misma respuesta final. | [`frontend/src/App.jsx`](../frontend/src/App.jsx) |
| `PDFPage` | Renderiza la página visible con PDF.js, limita canvas y pinta boxes solo si dimensiones coinciden. | [`frontend/src/App.jsx`](../frontend/src/App.jsx) |
| `extract`, `selectStage`, `reset` | Envía FormData al endpoint, maneja abort/cambios de generación, consulta etapas opcionales. | [`frontend/src/App.jsx`](../frontend/src/App.jsx) |
| `displayBox` | Convierte puntos crop-relative sin rotar a porcentajes de pantalla y transforma rotaciones 0/90/180/270. | [`frontend/src/geometry.js`](../frontend/src/geometry.js) |
| Proxy/headers/static | Sirve assets, CSP, body limit y proxy API/preview. | [`frontend/nginx.conf`](../frontend/nginx.conf) |

El preview es un apoyo de auditoría del **último** request de un proceso, no un
historial ni interfaz multiusuario. La comparación del PDF impide mostrar una
etapa que no corresponda al archivo/resultados actuales; no protege una API
expuesta a usuarios no confiables.

### Setup, mantenimiento y pruebas de humo (no corren en cada request)

| Archivo / funciones | Alcance | Código |
|---|---|---|
| `read_private_key`, `create_environment`, `main` | Setup interactivo; lee clave sin eco, exige consentimiento para Gemini y crea `.env` sin abrir/reemplazar uno existente. | [`scripts/setup_env.py`](../scripts/setup_env.py) |
| `clean_attempt`, `run`, `main` + wrapper shell | Herramienta operativa de dry-run/listado y limpieza acotada; el shell usa el Compose ya activo, no lo reinicia. | [`cleanup_ingest.py`](../app/maintenance/cleanup_ingest.py), [`cleanup_ingest.sh`](../scripts/cleanup_ingest.sh) |
| `check`, `wait_for_server`, `post`, `synthetic_rotated_pdf`, `main` | HTTP real loopback contra Uvicorn, status/input inválidos, cuerpo grande y PDF sintético rotado/recortado. | [`verificar_http.py`](../scripts/smoke/verificar_http.py) |
| `check`, `main` | OCR, spans NER, offsets, cajas y fuzzy con componentes reales sobre el PDF de ejemplo; no es benchmark general. | [`verificar_componentes.py`](../scripts/smoke/verificar_componentes.py) |
| `check`, `ready`, `post`, `scanned_with_empty_first_page`, `verify_retrieval`, `verify_retrieval_concurrency`, `inspect_stage`, `send_ask`, `gated_query`, `fail_manifest`, `send_then_timeout` | Comprueba ingestión/Qdrant/ledger/retrieval; closures simulan respuestas, gates y fallos para estados/cancelación. | [`verificar_ingestion.py`](../scripts/smoke/verificar_ingestion.py) |
| `post`, `synthetic_pdf`, `ask`, `main`; `cleanup` (shell) | Smoke end-to-end RAG sobre corpus sintético con generador simulado. | [`verificar_rag_e2e.py`](../scripts/smoke/verificar_rag_e2e.py), [`rag_e2e.sh`](../scripts/smoke/rag_e2e.sh) |
| `call`, `synthetic_pdf`, `main`; `cleanup` (shell) | Reproduce evaluación sintética con Gemini real; genera llamadas externas/costo potencial, no es suite local determinista. | [`verificar_gemini_real.py`](../scripts/smoke/verificar_gemini_real.py), [`gemini_real.sh`](../scripts/smoke/gemini_real.sh) |
| `require`, `signature`, `collect`, `compare`, `render`, `main`; `cleanup` (shell) | Compara etapas y niveles de recuperación sobre caso de prueba preparado. | [`verificar_recuperacion_nombres.py`](../scripts/smoke/verificar_recuperacion_nombres.py), [`recuperacion_nombres.sh`](../scripts/smoke/recuperacion_nombres.sh) |
| `require`, `get`, `collect`, `render`, `main`; `cleanup` (shell) | Comprueba stages del preview y render visual. | [`verificar_vista_etapas.py`](../scripts/smoke/verificar_vista_etapas.py), [`vista_etapas.sh`](../scripts/smoke/vista_etapas.sh) |
| `require`, `describe`, `ingest_sample`, `check_vector`, `ingest_blank_page`, `snapshot`, `verify_snapshot`, `main`; `cleanup`, `pause`, `create_owned_volume`, `wait_for_http` (shell) | Recorrido visual de estados/vector/páginas y snapshots; usa recursos temporales para observar persistencia. | [`recorrido_visual.py`](../scripts/smoke/recorrido_visual.py), [`recorrido_visual.sh`](../scripts/smoke/recorrido_visual.sh) |
| `FakeGenerator.generate`, `fake_service` | Proveedor de prueba dentro del proceso para RAG sin llamadas externas. | [`serve_ask_fake.py`](../scripts/smoke/serve_ask_fake.py) |
| `usage`, `cleanup`, `components`, `http` | Construye/ejecuta/limpia contenedor temporal para componentes y HTTP; solo detiene el recurso que creó. | [`ejecutar.sh`](../scripts/smoke/ejecutar.sh) |
| `cleanup`, `create_owned_volume`, `start_qdrant`, `start_api`, `check_stage` | Smoke aislado de ingestión/persistencia con red y volúmenes temporales propios; cleanup valida ownership antes de borrar. | [`ingestion.sh`](../scripts/smoke/ingestion.sh) |
| Copia de `cmaps`, `standard_fonts`, `wasm` | Incluye assets locales que PDF.js requiere en runtime. | [`copy-pdf-assets.mjs`](../frontend/scripts/copy-pdf-assets.mjs) |
| `make test`, `smoke-extract`, `smoke-rag`, `test-rag-real` | Comandos para suites unit/integration, smoke con fake y llamada real externa. Las funciones de tests no son lógica de producción y se leen en sus archivos específicos. | [`Makefile`](../Makefile), [`tests/`](../tests/), [`frontend/tests/`](../frontend/tests/) |

## 9. Inventario function-by-function

Este catálogo nombra las funciones de `app/` y la interfaz mantenida. La
explicación de la decisión y el recorrido se encuentra en las secciones de flujo;
esta tabla permite abrir la función concreta. Los constructores generados
automáticamente por `dataclass`/Pydantic no se enumeran como código escrito.

### Bootstrap, HTTP y validación

| Archivo / funciones | Función y alcance |
|---|---|
| [`main.py`](../app/main.py): `lifespan`, `health` | `lifespan` crea y cierra servicios compartidos por proceso; `health` reporta liveness de la API, no readiness de dependencias. |
| [`api/dependencies.py`](../app/api/dependencies.py): `get_ocr_service`, `get_ner_service`, `get_extraction_service`, `get_ingestion_service`, `get_retrieval_service`, `get_ask_service` | Recuperan objetos del estado de aplicación; `get_extraction_service`/`get_ask_service` ensamblan dependencias sin cargar modelos en el router. |
| [`api/extract.py`](../app/api/extract.py): `extract` | Adaptador HTTP para validar multipart/names, delegar y mapear excepciones a HTTP. |
| [`api/rag.py`](../app/api/rag.py): `ingest`, `ask` | Adaptadores separados para PDF multipart y pregunta JSON; no contienen el algoritmo de OCR, persistencia o recuperación. |
| [`api/extraction_form.py`](../app/api/extraction_form.py): `_ExtractionMultipartParser.__init__`, `on_part_data`, `on_end`, `close_extraction_form`, `parse_extraction_form` | Parser incrementa límites por parte, detecta boundary final y cierra uploads/temporales bajo cancelación. |
| [`api/extraction_preview.py`](../app/api/extraction_preview.py): `_authorized_store`, `latest_preview`, `preview_document`, `preview_stage` | Autorizan con bearer, obtienen solo el preview vigente y devuelven PDF/stages sin cache. |
| [`core/config.py`](../app/core/config.py): `validate_ingestion_limits`, `validate_extract_preview`, `validate_llm`, `validate_ner_model`, `validate_name_recovery_passes`, `validate_log_level`, `get_settings` | Validan combinaciones/configuración sensible y cachean la instancia de `Settings`. |
| [`core/request_limits.py`](../app/core/request_limits.py): `_route_path`, `_declared_length_status`, `ExtractionBodyLimitMiddleware.__init__`, `__call__`, `limited_receive`, `tracked_send` | Resuelve ruta ASGI, valida Content-Length, cuenta body real, limita concurrencia y evita segunda respuesta después de iniciar una. |
| [`services/extraction_input.py`](../app/services/extraction_input.py): `_reject_json_constant`, `parse_names` | Impide constantes no estándar JSON y valida `NamePair`/bytes/items/chars sin incluir el input en errores. |
| [`services/ask_input.py`](../app/services/ask_input.py): `normalize_question` | Rechaza pregunta vacía/excesiva y quita whitespace exterior; no altera su significado. |

### Core, contratos y utilidades

| Archivo / funciones | Función y alcance |
|---|---|
| [`core/local_storage.py`](../app/core/local_storage.py): `private_directory`, `private_file`, `file_lock` | Crea/verifica storage privado, rechaza symlinks/archivos no regulares y toma `flock` same-host. |
| [`core/worker_environment.py`](../app/core/worker_environment.py): `worker_environment` | Construye environment de subprocess sin preview token ni API key LLM. |
| [`core/logging.py`](../app/core/logging.py): `JsonFormatter.format`, `configure_logging` | Serializa log UTC JSON y configura niveles de `app`/Uvicorn. |
| [`models/document.py`](../app/models/document.py): `OCRDocument.text` | Concatena texto de páginas para consumidores que necesitan texto plano; no reemplaza metadatos page-local. |
| [`models/extraction.py`](../app/models/extraction.py): `PersonSpan.__post_init__` | Impone texto y rango no vacío/no negativo; la correspondencia con una página se verifica después. |
| [`models/schemas.py`](../app/models/schemas.py) | Define `BoundingBox`, `ExtractedName`, `FuzzyMatch`, `NamePair`, `ExtractionResponse`, `IngestResponse`, `RAGRequest`, `RAGResponse`, `HealthResponse`: contratos HTTP, no modelos de inferencia. |

### Extracción, ubicación, fuzzy y preview

| Archivo / funciones | Función y alcance |
|---|---|
| [`services/extraction_service.py`](../app/services/extraction_service.py): `ExtractionService.__init__`, `_preview_pdf`, `extract`, `_assemble` | Inyecta OCR/NER; lee copia acotada opcional para preview; orquesta OCR/NER y assembly CPU, asociación segura, recuperación, límites fuzzy y respuesta. |
| [`services/bbox_service.py`](../app/services/bbox_service.py): `_valid_word`, `_adjacent`, `PageLocator.__init__`, `locate`, `locate_person_span`, `_locate`, `find_name_bounding_boxes` | Valida OCR y cercanía geométrica, busca palabras por offsets y une boxes. `find_name_bounding_boxes` es helper legacy síncrono que vuelve a OCR. |
| [`services/fuzzy_service.py`](../app/services/fuzzy_service.py): `normalize_name`, `match_names`, `fuzzy_match_names` | Normaliza para comparar; `match_names` ejecuta full-string ratio y conserva orden; wrapper legacy carga configuración global. |
| [`services/name_recovery.py`](../app/services/name_recovery.py): `_Budget.__init__/window/compare/normalize`, `_Intervals.__init__/overlaps/extend`, `_at_most_one_edit`, `_fuzzy_match`, `recover_names`, `has_content`, `flush_cluster` | Cuenta trabajo; fusiona rangos protegidos; evalúa edición/fuzzy; las closures cachean palabras y agrupan candidatos solapados; rechaza ambigüedad. |
| [`services/extraction_preview.py`](../app/services/extraction_preview.py): `LatestExtractionPreview.__init__/authorized/begin/is_current/publish/_expire/get/close`, `build_preview_stages`, `by_page` | Valida token; invalida/publica una generación; limita y expira memoria; serializa overlays por etapa. `by_page` es helper interno del builder. |

### OCR y NER

| Archivo / funciones | Función y alcance |
|---|---|
| [`services/ocr_service.py`](../app/services/ocr_service.py): `_validate_pdf_size`, `_word_box`, `_mean_confidence` | Valida bytes, transforma cajas imagen→PDF y promedia confianza de palabras legibles. |
| [`services/ocr_service.py`](../app/services/ocr_service.py): `OCRService.__init__`, `_admit`, `extract_async`, `extract_file_async`, `extract_stream_async`, `_schedule`, `finished`, `_run_admitted`, `_execute_job`, `aclose` | Admisión, cola, deadlines, subprocess temporal, supervisión y cierre para callers async. |
| [`services/ocr_service.py`](../app/services/ocr_service.py): `extract`, `extract_file`, `_extract_page` | Camino síncrono para callers locales/tests; abre/renderiza PDF y construye `OCRPage` con offsets/words. |
| [`services/ocr_service.py`](../app/services/ocr_service.py): `_finish_cancelled_task`, `_run_blocking`, `_start_worker`, `_terminate_process_group`, `_read_worker_result`, `_document_from_dict`, `extract_text_from_pdf`, `get_word_bounding_boxes` | Espera operaciones no-cancelables, administra grupo POSIX, decodifica worker y ofrece wrappers legacy. |
| [`services/ocr_worker.py`](../app/services/ocr_worker.py): `main` | Lee rutas/config del padre, ejecuta OCR y escribe un resultado JSON privado en el directorio temporal. |
| [`services/ner_service.py`](../app/services/ner_service.py): `validate_pages`, `SpacyPersonRecognizer.__init__/load/detect_page/detect_pages`, `_default_recognizer`, `extract_names` | Valida inputs, carga modelo instalado, devuelve spans `PERSON` por página; wrappers son síncronos. |
| [`services/ner_async.py`](../app/services/ner_async.py): `_terminate_worker`, `_start_worker`, `_send`, `_receive`, `_parse_result` | Ciclo de vida/JSON-lines del worker y validación de respuesta contra texto original. |
| [`services/ner_async.py`](../app/services/ner_async.py): `_WorkerSlot.__init__/detect/reset/close` | Un proceso spaCy persistente por slot; invalida slot en fallo y recolecta proceso al cerrar. |
| [`services/ner_async.py`](../app/services/ner_async.py): `NERService.__init__/detect_pages/_run_admitted/_execute_job/_shutdown/aclose`, `finished` | Pool/cola por proceso, deadlines, cancelación supervisada y liberación de slots. |
| [`services/ner_worker.py`](../app/services/ner_worker.py): `_send`, `_process_request`, `main` | Carga spaCy una vez, procesa requests sucesivos y evita escribir documento/model errors a stderr. |
| [`services/ner_protocol.py`](../app/services/ner_protocol.py) | `MAX_NER_RESPONSE_BYTES` define el máximo del protocolo del worker. |

### Chunking, embeddings e ingestión

| Archivo / funciones | Función y alcance |
|---|---|
| [`services/chunking_service.py`](../app/services/chunking_service.py): `Tokenizer.encode`, `TokenAwareChunker.__init__/_count/split_page` | Contrato tokenizer; comprueba budget especial; produce fragmentos word-boundary por página con offsets/overlap. |
| [`core/embedding_model.py`](../app/core/embedding_model.py): `verify_model_files`, `verified_model_snapshot`, `provision_model` | Verifica hashes, crea copia local privada allowlisted y provisiona/valida revisión al construir imagen. |
| [`services/embedding_service.py`](../app/services/embedding_service.py): `EmbeddingModel.__init__/close/encode`, `_get_default_model`, `get_embeddings`, `get_query_embedding` | Carga/usa SentenceTransformer local; valida entradas/vector; wrappers síncronos de compatibilidad. |
| [`services/embedding_async.py`](../app/services/embedding_async.py): `EmbeddingService.__init__/_receive/_send/prepare/embed_query/_run/_reset/aclose` | IPC JSON-lines, una instancia persistente; prepara páginas o embedding de query, valida respuesta y limpia proceso. |
| [`services/embedding_worker.py`](../app/services/embedding_worker.py): `send`, `prepare`, `process`, `main` | Serializa bounded output; genera chunks+embeddings o un vector de query; carga modelo una vez. |
| [`services/ingestion_service.py`](../app/services/ingestion_service.py): `processing_signature`, `safe_filename`, `error_code`, `IngestionService.__init__/_initialize/ingest/_run_with_deadline/_run/aclose` | Firma reproducible de receta, metadata segura, error ledger, admisión y pipeline completo con cleanup. |
| [`services/ingestion_vectors.py`](../app/services/ingestion_vectors.py): `endpoint_key`, `chunk_id`, `IngestionVectorStore.__init__/_call/_condition/attempt_filter/ensure/upsert/count/verify/clear_points/aclose` | Identifica endpoint/fragmento; opera collection y puntos, clasifica errores, verifica/borra scope preciso. |
| [`services/ingestion_ledger.py`](../app/services/ingestion_ledger.py): `_now`, `_uuid`, `_digest`, `_label`, `IngestionLedger.__init__/lock/attempt_lock/_connect/_transaction/_verify/initialize/_initialize_locked` | Validación de identidad/formatos, locks, conexión segura, transacciones/schema y creación del ledger. |
| [`services/ingestion_ledger.py`](../app/services/ingestion_ledger.py): `create/get/find_by_content/pending/record_pages/pages/set_chunk_counts` | Escribe/consulta intento, inventario y registro de cada página física, incluso vacía. |
| [`services/ingestion_ledger.py`](../app/services/ingestion_ledger.py): `_require_state/start_writing/complete/fail/write_intent/write_confirmed/_write_status/recover/mark_cleaned/identity` | Hace cumplir máquina de estados y registra incertidumbre/reconciliación; `identity` crea o exige identidad del ledger. |

### Preguntas, retrieval y mantenimiento

| Archivo / funciones | Función y alcance |
|---|---|
| [`services/ask_service.py`](../app/services/ask_service.py): `QueryEmbedder.embed_query`, `EligibleRetriever.retrieve`, `AnswerGenerator.generate` | Protocolos intercambiables, no implementación concreta. |
| [`services/ask_service.py`](../app/services/ask_service.py): `AskService.__init__/ask` | Valida límites, hace embed/retrieval, crea context IDs y verifica forma/citas literales. |
| [`services/ask_retrieval.py`](../app/services/ask_retrieval.py): `LedgerRetriever.__init__/retrieve` | Valida vector y candidatos; combina Qdrant con ledger/page metadata; devuelve solo fragmentos de ingestas `completed`. |
| [`services/ask_retrieval.py`](../app/services/ask_retrieval.py): `ManagedRetrievalService.__init__/retrieve/_retrieve/aclose` | Provee client por request, respeta cancelación y cierra cliente; no crea corpus como efecto de una pregunta. |
| [`services/answer_generator.py`](../app/services/answer_generator.py): `_gemini_content`, `HTTPAnswerGenerator.__init__/_request/generate/aclose` | Extrae contenido visible Gemini, aplica retry/timeouts/limit de bytes, adapta proveedores y cierra HTTPX. |
| [`maintenance/cleanup_ingest.py`](../app/maintenance/cleanup_ingest.py): `clean_attempt`, `run`, `main` | Lista pendientes o hace dry-run; exige ID, locks y confirmación de quiescencia para cleanup de escritura incierta. |
| [`services/rag_service.py`](../app/services/rag_service.py): `chunk_text`, `generate_answer`; [`services/vector_service.py`](../app/services/vector_service.py): `init_collection`, `store_document_chunks`, `search_similar` | Stubs originales que lanzan `NotImplementedError`; no se llaman desde rutas implementadas. |

### Scaffolds que no forman parte del camino HTTP activo

- [`services/rag_service.py`](../app/services/rag_service.py): `chunk_text` y
  `generate_answer` aún levantan `NotImplementedError`; es el scaffold original.
- [`services/vector_service.py`](../app/services/vector_service.py):
  `init_collection`, `store_document_chunks` y `search_similar` también son
  placeholders. El camino implementado usa `IngestionService`,
  `IngestionVectorStore`, `IngestionLedger`, `LedgerRetriever` y `AskService`.
- No describir los placeholders como fallos de los endpoints; sí reconocer que
  convendría retirarlos o marcarlos claramente para evitar dos arquitecturas
  aparentes.

## 10. Límites que no se deben sobreprometer

### Tests exactos para seguir las decisiones

| Tema | Tests fuente | Qué prueban / qué no |
|---|---|---|
| Contrato y orquestación de extracción | [`test_extraction_contracts.py`](../tests/unit/test_extraction_contracts.py), [`test_extraction_service.py`](../tests/unit/test_extraction_service.py), [`test_extract_endpoint.py`](../tests/integration/test_extract_endpoint.py) | Contratos, dependencias simuladas y endpoint; no precisión general NER. |
| HTTP/multipart/límites | [`test_extraction_request_limits.py`](../tests/integration/test_extraction_request_limits.py), [`test_async_http.py`](../tests/integration/test_async_http.py) | Fragmentación, lengths, límites/tiempo y servicio HTTP; no tráfico de producción. |
| OCR y coordenadas | [`test_ocr_service.py`](../tests/unit/test_ocr_service.py), [`test_ocr_real.py`](../tests/integration/test_ocr_real.py), [`test_bbox_and_fuzzy.py`](../tests/unit/test_bbox_and_fuzzy.py) | Lógica simulada, integración con motores reales y geometría conocida; conjunto real pequeño. |
| NER y recuperación de nombres | [`test_ner_service.py`](../tests/unit/test_ner_service.py), [`test_ner_async.py`](../tests/unit/test_ner_async.py), [`test_ner_real.py`](../tests/integration/test_ner_real.py), [`test_name_recovery.py`](../tests/unit/test_name_recovery.py), [`test_name_recovery_bullet_real.py`](../tests/integration/test_name_recovery_bullet_real.py) | Model/worker/heurística y algunos PDFs; no generaliza a idiomas/documentos no evaluados. |
| Embeddings y chunking | [`test_chunking_service.py`](../tests/unit/test_chunking_service.py), [`test_embedding_model.py`](../tests/unit/test_embedding_model.py), [`test_embedding_service.py`](../tests/unit/test_embedding_service.py), [`test_embedding_async.py`](../tests/unit/test_embedding_async.py), [`test_embedding_foundations_real.py`](../tests/integration/test_embedding_foundations_real.py) | Límite de tokens, artefactos/vector/IPC y un paso con modelo local real. |
| Ledger, Qdrant e ingestión | [`test_ingestion_ledger.py`](../tests/unit/test_ingestion_ledger.py), [`test_ingestion_vectors.py`](../tests/unit/test_ingestion_vectors.py), [`test_ingestion_service.py`](../tests/unit/test_ingestion_service.py), [`test_ingest_endpoint.py`](../tests/integration/test_ingest_endpoint.py) | Estados, errores/fakes y endpoint; contrasta real persistence con smoke/E2E registrados. |
| Retrieval, answer y provider | [`test_ask_service.py`](../tests/unit/test_ask_service.py), [`test_ask_retrieval.py`](../tests/unit/test_ask_retrieval.py), [`test_answer_generator.py`](../tests/unit/test_answer_generator.py), [`test_ask_endpoint.py`](../tests/integration/test_ask_endpoint.py) | Elegibilidad, límites y transportes simulados; el resultado real Gemini está separado en el informe RAG. |
| Frontend/geometría | [`geometry.test.mjs`](../frontend/tests/geometry.test.mjs), [`test_extraction_preview.py`](../tests/integration/test_extraction_preview.py) | Transformación de boxes y preview; no reemplaza revisión visual humana. |

1. **Calidad OCR/NER:** evidencia humana de cajas solo cubre los documentos
   registrados; modelo spaCy inglés puede omitir o etiquetar de más. Véase
   [manual test log](MANUAL_TEST_LOG.md) y [test log](TEST_LOG.md).
2. **Recuperación:** aumenta recall de ocurrencias repetidas usando nombres NER
   seed; puede propagar etiquetas incorrectas o localizar un homónimo. Es
   configurable y no reemplaza evaluación de precisión/recall.
3. **RAG:** los valores de chunk son baseline, no resultado de tuning. La
   [evaluación sintética](EVALUACION_FUENTES_RAG.md) tiene 25 preguntas, un caso
   semántico incorrecto y un parcial; fue una ejecución pequeña, no benchmark.
   El prompt se ajustó después y esa versión no se volvió a evaluar con provider
   real según esa evidencia.
4. **Consistencia/escalado:** ledger local y Qdrant no son transacción distribuida;
   locks son same-host/cooperating. No hay límite de concurrencia global,
   multi-tenant auth, SLA ni garantía exactly-once.
5. **Privacidad:** la ruta de generación entrega preguntas y fragmentos al
   proveedor externo cuando se habilita. La app no ofrece autorización por
   usuario; no exponerla a tráfico no confiable.
6. **Citas:** comprobar una subcadena literal no demuestra entailment ni elimina
   prompt injection; human review sigue necesaria para casos de alto impacto.

## 11. Preguntas de entrevista para estudiar con el código abierto

- Sigue [`extract`](../app/api/extract.py) hasta [`PageLocator._locate`](../app/services/bbox_service.py):
  ¿cómo evitas atribuir una caja de otra página o de palabras distantes?
- En [`OCRService._word_box`](../app/services/ocr_service.py): ¿qué sistema de
  coordenadas entrega Tesseract y cómo se convierte a puntos PDF bajo rotación?
- En [`recover_names`](../app/services/name_recovery.py): ¿qué sube/baja,
  precisión o recall, y cómo se bloquean ambigüedades?
- En [`TokenAwareChunker`](../app/services/chunking_service.py): ¿por qué contar
  tokens especiales, preservar palabras y no truncar?
- En [`IngestionService._run`](../app/services/ingestion_service.py): ¿qué
  ocurre entre SQLite y un timeout de Qdrant? ¿Por qué `write_intent` precede
  el upsert?
- En [`LedgerRetriever.retrieve`](../app/services/ask_retrieval.py): ¿por qué
  recuperar candidatos no basta y se verifica `completed` con SQLite?
- En [`AskService.ask`](../app/services/ask_service.py): ¿qué valida la quote y
  qué no puede validar semánticamente?
- A partir de [`EVALUACION_FUENTES_RAG.md`](EVALUACION_FUENTES_RAG.md): ¿qué
  métrica/dataset construirías antes de cambiar modelo, chunk size o prompt?

La mejor defensa técnica es distinguir con precisión **lo implementado, lo
medido, lo simulado y lo pendiente**, y explicar un trade-off que cambiarías con
evidencia adicional.
