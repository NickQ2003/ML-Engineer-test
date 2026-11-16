# ML Engineer Take-Home — Document Intelligence Service

Build, from scratch, a small production-quality service that extracts person
names (with their locations) from scanned PDFs, fuzzy-matches them against a
provided list, and answers questions about ingested documents using RAG
(Retrieval-Augmented Generation).

This repository started as an empty skeleton. The implemented extraction flow
and remaining RAG work are described below. We care as much about *how* it is
built (design, tests, engineering practices, decisions) as whether it runs.

## Why this exists (the problem)

Let's imagine a company that processes large volumes of scanned documents (contracts, KYC forms,
filings) that mention people. For **compliance**, it isn't enough to know *that*
a name appears — a reviewer has to be able to **find it on the page** and
**double-check that specific name against other records** (an ID, a sanctions
list, an internal system). A name with no location is not auditable.

So the core requirement is: **given a PDF, return each person's name together
with exactly where it sits on the page** (page number + bounding box). That
location is what lets a human or a downstream system verify the match against
other information. The fuzzy-matching and RAG pieces build on the same extracted
content. Keep this "locate it so it can be verified" purpose in mind — it's why
bounding-box accuracy and correct pages matter more than they might first seem.

---

## What you're building

A FastAPI service exposing four endpoints. The request/response **contract is
fixed** by `app/models/schemas.py` — do not change those shapes (add fields only
if you have a reason and document it).

### 1. `GET /health`
Liveness probe. Returns `{"status": "ok"}` (plus anything else useful).

### 2. `POST /api/extract`  — names + bounding boxes + fuzzy match
`multipart/form-data`:
- `pdf_file`: a (scanned) PDF.
- `names`: a JSON string — a list of `{"first_name": ..., "last_name": ...}`.

Pipeline:
1. **OCR** the PDF (it is scanned — there is no selectable text layer).
2. **NER**: extract **person** names only (no organizations, places, dates).
3. **Bounding boxes**: locate each extracted name in the page and return its box
   in **PDF coordinate space** (points, 72 per inch — *not* raw image pixels),
   with a **0-based page number**.
4. **Fuzzy match** the extracted names against the provided `names`.

Response: `ExtractionResponse` (see `schemas.py`) — `extracted_names[]` with a
`bounding_box`, and `fuzzy_matches[]`.

### 3. `POST /api/ingest` — index a document for RAG
`multipart/form-data` with `pdf_file`. OCR → chunk → embed → store in a vector
database. Response: `IngestResponse` (`status`, `chunks_stored`).

### 4. `POST /api/ask` — answer a question with RAG
JSON `{"question": "..."}`. Embed the question, retrieve the most relevant
chunks, call an LLM to answer **grounded in the retrieved context**, and return
`RAGResponse` (`answer`, `sources`).

---

## Requirements that matter (this is the actual evaluation)

> **Scope & time.** This is time-boxed — we do **not** expect fully isolated
> infrastructure. An **embedded / in-process vector store (e.g. Chroma) running
> locally is completely fine**, and you do not need Docker to pass. Things like a
> `docker-compose.yml` with an isolated vector DB, a hardened container, or a
> clean separation of services are **not required** — but if you add them, they
> count as **positive extras** in the evaluation. Prioritize a correct,
> well-structured core; reach for the infra polish only if you have time.

Anyone can wire libraries together. We're hiring an **ML Engineer**, so we look
for production judgment:

- **Architecture** — SOLID, dependency injection, separation of concerns.
  Services depend on abstractions (Protocols/ABCs), not concrete singletons.
  HTTP routers stay thin; business logic is testable without HTTP.
- **Configuration** — no hardcoded hosts/ports/model names/thresholds/DPI.
  Centralized, environment-overridable config.
- **Resilience** — timeouts and retries on external calls (LLM, vector store);
  meaningful failure modes; no silent `except: pass`.
- **Validation** — reject non-PDFs (by content, not just extension), empty
  files, oversized uploads; validate request bodies.
- **Resource management** — no leaked file handles or temp files.
- **Observability** — structured logging with useful context.
- **Correctness details we will check**, e.g.:
  - OCR must process **every** page (including the first).
  - Bounding boxes must be in **PDF space**, not image pixels.
  - Name matching must be **case-insensitive** and robust to OCR noise and
    accents (`María González` ~ `Maria Gonzalez`); prefer full-string
    similarity over substring tricks.
  - RAG chunking must not cut words in half; retrieval must be grounded (no
    hallucinated `sources`).
  - Vector-store IDs must be unique across documents (a second ingest must not
    overwrite the first).
- **Tests** — you write them. Cover the tricky logic above with unit tests
  (mock the heavy models / external services) and at least one end-to-end path.
  We look at *what* you chose to test and how.
- **Containerization (bonus, not required)** — if you have time, a `Dockerfile`
  and a `docker-compose.yml` that brings up the app **and its dependencies** with
  `docker compose up` (and `GET /health` passing) is a strong positive. Skipping
  it costs you nothing on the core; a local/embedded setup is fine.
- **Audit viewer (bonus, not required)** — a simple frontend that, given a PDF,
  **renders the pages and draws the returned bounding boxes over the detected
  names**. This is the compliance reviewer's tool: it's how a human confirms a
  name really is where the API says it is. Nothing fancy — a single self-
  contained page (e.g. vanilla JS + pdf.js) that calls `/api/extract` and
  overlays the boxes is plenty. Strong positive if included.
- **DESIGN.md** (≤ 3 pages) — component + sequence view, technology choices and
  trade-offs, how you'd scale to 1000+ PDFs/hour, failure modes and mitigations,
  and — if you were to route between models by query complexity — how.

---

## Suggested stack (substitute freely, but justify it in DESIGN.md)

- Python 3.12, FastAPI, `pytest`
- OCR: Tesseract via `pytesseract` + `PyMuPDF` (PDF → image)
- NER: spaCy (`en_core_web_sm`); handle non-English if you add it
- Fuzzy: `thefuzz`
- Embeddings: `sentence-transformers`
- Vector DB: **Chroma** (embedded/local — simplest) or Qdrant if you prefer a
  separate service
- LLM: any OpenAI-compatible endpoint (configurable base URL + key)

Dependencies live in `pyproject.toml` and are managed with **[uv](https://docs.astral.sh/uv/)** — change them as you see fit (`uv add ...`). Commit the resulting `uv.lock`.

---

## Deliverables

1. A **branch + pull request** against this repo with your implementation.
2. `DESIGN.md`.
3. A short **`DECISIONS.md`** — notable engineering decisions and trade-offs
   (bullets are fine). This is where your reasoning earns credit.
4. Tests (required). `Dockerfile` / `docker-compose.yml` are **optional bonus**.

## How we evaluate (weights)

| Criterion | Weight |
|---|---|
| Correctness — the pipeline works on the sample PDFs | 30% |
| Architecture & design (SOLID, DI, abstractions, thin routers) | 20% |
| Engineering practices (config, logging, resilience, validation) | 20% |
| Code quality & decisions | 15% |
| Tests you wrote (coverage of the tricky logic) | 10% |
| Design document | 5% |

**Positive extras (bonus, on top of the above):** an **audit viewer** that
overlays the bounding boxes on the PDF, containerization (`Dockerfile` /
`docker-compose.yml`), a truly isolated vector DB, multi-language support,
batching, or other production hardening. Not required — credited when present.

## Getting started

```bash
uv sync --extra dev                          # create .venv and install deps from pyproject.toml
# The locked spaCy model is installed by uv sync; no separate download command.
# Vector DB: embedded Chroma needs nothing extra. Only if you choose Qdrant:
#   docker run -p 6333:6333 qdrant/qdrant
uv run uvicorn app.main:app --reload
```

Sample scanned PDFs are in `sample_pdfs/`.

## Use of AI tools

Using AI assistants (Copilot, Claude, ChatGPT, agents) is **allowed and
expected** — that's how modern engineering works. Two conditions:

1. **Disclose it** in `DECISIONS.md` (what you used it for).
2. **Own every line.** In a follow-up conversation we will ask you to explain
   design choices, walk through your code, and make a live change. Code you
   can't reason about will not count in your favor.

We are evaluating *your* engineering judgment, not an agent's output. Submitting
something you don't understand is the one thing that fails this test.

---

# Prueba técnica para ML Engineer — Servicio de inteligencia documental

Construye desde cero un pequeño servicio con calidad de producción que extraiga
nombres de personas (con su ubicación) de archivos PDF escaneados, los compare
mediante coincidencia aproximada con una lista proporcionada y responda preguntas
sobre los documentos incorporados utilizando RAG (generación aumentada por
recuperación).

Este repositorio es una **estructura vacía**. No hay código funcional que
corregir: debes implementar todo. Nos importa tanto *cómo* lo construyes (diseño,
pruebas, prácticas de ingeniería y decisiones) como que funcione.

## Por qué existe esta prueba (el problema)

Imaginemos una empresa que procesa grandes volúmenes de documentos escaneados
(contratos, formularios KYC y documentos presentados ante organismos oficiales)
que mencionan personas. Por motivos de **cumplimiento normativo**, no basta con
saber *que* aparece un nombre: una persona revisora debe poder **encontrarlo en
la página** y **verificar ese nombre específico frente a otros registros** (un
documento de identidad, una lista de sanciones o un sistema interno). Un nombre
sin ubicación no se puede auditar.

Por tanto, el requisito principal es: **dado un PDF, devolver el nombre de cada
persona junto con su ubicación exacta en la página** (número de página y cuadro
delimitador). Esa ubicación permite que una persona o un sistema posterior
verifique la coincidencia con otra información. La coincidencia aproximada y RAG
se basan en el mismo contenido extraído. Ten presente este propósito de «localizar
para verificar»: por eso la precisión de los cuadros delimitadores y la
identificación correcta de las páginas son más importantes de lo que podría
parecer inicialmente.

---

## Qué vas a construir

Un servicio FastAPI que exponga cuatro endpoints. El **contrato** de
solicitud/respuesta está **fijado** por `app/models/schemas.py`: no cambies esas
estructuras (añade campos solo si tienes un motivo y lo documentas).

### 1. `GET /health`

Comprobación de disponibilidad. Devuelve `{"status": "ok"}` (y cualquier otra
información útil).

### 2. `POST /api/extract` — nombres + cuadros delimitadores + coincidencia aproximada

`multipart/form-data`:
- `pdf_file`: un PDF escaneado.
- `names`: una cadena JSON con una lista de `{"first_name": ..., "last_name": ...}`.

Flujo de procesamiento:
1. Aplicar **OCR** al PDF (está escaneado: no tiene una capa de texto seleccionable).
2. **NER**: extraer únicamente nombres de **personas** (no organizaciones, lugares ni fechas).
3. **Cuadros delimitadores**: localizar cada nombre extraído en la página y
   devolver su cuadro en el **sistema de coordenadas del PDF** (puntos, 72 por
   pulgada; *no* píxeles de la imagen), con un **número de página que empieza en 0**.
4. Comparar los nombres extraídos con los `names` proporcionados mediante
   **coincidencia aproximada**.

Respuesta: `ExtractionResponse` (consulta `schemas.py`), con `extracted_names[]`
y su `bounding_box`, y `fuzzy_matches[]`.

### 3. `POST /api/ingest` — indexar un documento para RAG

`multipart/form-data` con `pdf_file`. OCR → división en fragmentos → generación
de embeddings → almacenamiento en una base de datos vectorial. Respuesta:
`IngestResponse` (`status`, `chunks_stored`).

### 4. `POST /api/ask` — responder una pregunta con RAG

JSON `{"question": "..."}`. Generar el embedding de la pregunta, recuperar los
fragmentos más relevantes, llamar a un LLM para responder **basándose en el
contexto recuperado** y devolver `RAGResponse` (`answer`, `sources`).

---

## Requisitos importantes (esta es la evaluación real)

> **Alcance y tiempo.** Esta prueba tiene un tiempo limitado: **no** esperamos
> infraestructura completamente aislada. Una **base de datos vectorial
> integrada / en el mismo proceso (por ejemplo, Chroma) que se ejecute localmente
> es totalmente aceptable**, y no necesitas Docker para aprobar. Elementos como
> un `docker-compose.yml` con una base de datos vectorial aislada, un contenedor
> reforzado o una separación clara de servicios **no son obligatorios**; sin
> embargo, si los añades, cuentan como **extras positivos** en la evaluación.
> Prioriza un núcleo correcto y bien estructurado; mejora la infraestructura
> solo si tienes tiempo.

Cualquiera puede conectar bibliotecas. Estamos contratando a un **ML Engineer**,
así que buscamos criterio para trabajar en producción:

- **Arquitectura** — SOLID, inyección de dependencias y separación de
  responsabilidades. Los servicios dependen de abstracciones (Protocols/ABC),
  no de singletons concretos. Los routers HTTP se mantienen simples; la lógica
  de negocio se puede probar sin HTTP.
- **Configuración** — no codifiques directamente hosts, puertos, nombres de
  modelos, umbrales ni DPI. La configuración debe estar centralizada y permitir
  sobrescribir valores mediante variables de entorno.
- **Resiliencia** — tiempos de espera y reintentos en llamadas externas (LLM,
  base de datos vectorial); fallos informativos; nada de `except: pass` silenciosos.
- **Validación** — rechaza archivos que no sean PDF (según su contenido, no solo
  su extensión), archivos vacíos y cargas demasiado grandes; valida los cuerpos
  de las solicitudes.
- **Gestión de recursos** — no dejes manejadores de archivos abiertos ni
  archivos temporales sin eliminar.
- **Observabilidad** — registros estructurados con contexto útil.
- **Detalles de corrección que comprobaremos**, por ejemplo:
  - El OCR debe procesar **todas** las páginas (incluida la primera).
  - Los cuadros delimitadores deben estar en el **sistema de coordenadas del PDF**,
    no en píxeles de la imagen.
  - La comparación de nombres debe **ignorar mayúsculas y minúsculas** y ser
    robusta frente a errores de OCR y acentos (`María González` ~ `Maria Gonzalez`);
    prefiere la similitud de la cadena completa a los trucos con subcadenas.
  - La división en fragmentos para RAG no debe cortar palabras por la mitad; la
    recuperación debe basarse en contenido real (sin `sources` inventadas).
  - Los identificadores de la base de datos vectorial deben ser únicos entre
    documentos (una segunda incorporación no debe sobrescribir la primera).
- **Pruebas** — debes escribirlas. Cubre la lógica compleja anterior con pruebas
  unitarias (simula los modelos pesados y los servicios externos) y al menos un
  flujo de extremo a extremo. Evaluamos *qué* decidiste probar y cómo.
- **Contenedorización (extra, no obligatoria)** — si tienes tiempo, un
  `Dockerfile` y un `docker-compose.yml` que inicien la aplicación **y sus
  dependencias** con `docker compose up` (y con `GET /health` funcionando) son
  un punto muy positivo. Omitirlos no penaliza la evaluación principal; una
  configuración local o integrada es suficiente.
- **Visor de auditoría (extra, no obligatorio)** — una interfaz sencilla que,
  dado un PDF, **muestre las páginas y dibuje los cuadros delimitadores devueltos
  sobre los nombres detectados**. Esta es la herramienta de quien revisa el
  cumplimiento: permite que una persona confirme que un nombre realmente está
  donde la API indica. No hace falta nada sofisticado: basta con una página
  autocontenida (por ejemplo, JavaScript puro + pdf.js) que llame a
  `/api/extract` y superponga los cuadros. Es un punto muy positivo si se incluye.
- **DESIGN.md** (≤ 3 páginas) — vistas de componentes y de secuencia, elecciones
  tecnológicas y sus ventajas y desventajas, cómo escalarías a más de 1000 PDF por
  hora, modos de fallo y mitigaciones y, si tuvieras que elegir entre modelos
  según la complejidad de la consulta, cómo lo harías.

---

## Tecnologías sugeridas (puedes sustituirlas, pero justifícalo en DESIGN.md)

- Python 3.12, FastAPI, `pytest`
- OCR: Tesseract mediante `pytesseract` + `PyMuPDF` (PDF → imagen)
- NER: spaCy (`en_core_web_sm`); contempla otros idiomas si los añades
- Coincidencia aproximada: `thefuzz`
- Embeddings: `sentence-transformers`
- Base de datos vectorial: **Chroma** (integrada/local, la opción más sencilla)
  o Qdrant si prefieres un servicio separado
- LLM: cualquier endpoint compatible con OpenAI (URL base y clave configurables)

Las dependencias están en `pyproject.toml` y se gestionan con
**[uv](https://docs.astral.sh/uv/)**; modifícalas como consideres conveniente
(`uv add ...`). Incluye en el commit el `uv.lock` resultante.

---

## Entregables

1. Una **rama y un pull request** a este repositorio con tu implementación.
2. `DESIGN.md`.
3. Un **`DECISIONS.md`** breve: decisiones de ingeniería relevantes y sus
   ventajas y desventajas (una lista con viñetas es suficiente). Aquí se valora
   tu razonamiento.
4. Pruebas (obligatorias). `Dockerfile` / `docker-compose.yml` son **extras opcionales**.

## Cómo evaluamos (ponderaciones)

| Criterio | Ponderación |
|---|---|
| Corrección: el flujo funciona con los PDF de ejemplo | 30 % |
| Arquitectura y diseño (SOLID, inyección de dependencias, abstracciones, routers simples) | 20 % |
| Prácticas de ingeniería (configuración, registros, resiliencia, validación) | 20 % |
| Calidad del código y decisiones | 15 % |
| Pruebas que escribiste (cobertura de la lógica compleja) | 10 % |
| Documento de diseño | 5 % |

**Extras positivos (adicionales a lo anterior):** un **visor de auditoría** que
superponga los cuadros delimitadores en el PDF, contenedorización (`Dockerfile` /
`docker-compose.yml`), una base de datos vectorial realmente aislada, soporte
para varios idiomas, procesamiento por lotes u otras mejoras para producción. No
son obligatorios; se valoran cuando están presentes.

## Primeros pasos

```bash
uv sync --extra dev                          # crear .venv e instalar dependencias de pyproject.toml
# El modelo spaCy fijado se instala con uv sync; no hace falta otra descarga.
# Base de datos vectorial: Chroma integrado no necesita nada adicional. Solo si eliges Qdrant:
#   docker run -p 6333:6333 qdrant/qdrant
uv run uvicorn app.main:app --reload
```

Los PDF escaneados de ejemplo están en `sample_pdfs/`.

## Uso de herramientas de IA

El uso de asistentes de IA (Copilot, Claude, ChatGPT, agentes) está **permitido
y se espera que los uses**: así funciona la ingeniería moderna. Hay dos condiciones:

1. **Decláralo** en `DECISIONS.md` (indica para qué los utilizaste).
2. **Hazte responsable de cada línea.** En una conversación posterior te
   pediremos que expliques las decisiones de diseño, recorras tu código y hagas
   un cambio en directo. El código cuyo funcionamiento no puedas razonar no
   contará a tu favor.

Evaluamos *tu* criterio de ingeniería, no el resultado de un agente. Entregar algo
que no comprendes es lo único que hace que no superes esta prueba.

---

## Docker development / Desarrollo con Docker

The initial infrastructure runs FastAPI and Qdrant as separate containers. Docker
Engine and Docker Compose are required. Qdrant data persists in the `qdrant_data`
volume; it is not exposed directly to the host. The first build downloads the
Python dependencies and requires network access.

La infraestructura inicial ejecuta FastAPI y Qdrant en contenedores separados.
Se necesitan Docker Engine y Docker Compose. Los datos de Qdrant persisten en el
volumen `qdrant_data`, sin exponer el servicio directamente al host. La primera
construcción descarga las dependencias de Python y necesita acceso a la red.

The Linux image installs the CPU-only PyTorch build; it does not require NVIDIA
drivers or CUDA. Changing to GPU acceleration requires a different build setup.

La imagen Linux instala PyTorch para CPU; no necesita controladores NVIDIA ni
CUDA. Utilizar aceleración por GPU requiere una configuración de build distinta.

```bash
# Optional / Opcional: customize defaults before starting / personalizar antes de iniciar
cp .env.example .env
docker compose up --build
```

In another terminal / En otra terminal:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

`GET /health` is a liveness check for the API, not a Qdrant readiness check.
`POST /api/extract` is implemented; the ingestion and question-answering RAG
endpoints and their Qdrant, embedding and LLM integrations remain pending.

`GET /health` comprueba que la API está viva, no que Qdrant esté preparado.
`POST /api/extract` está implementado; los endpoints RAG de ingestión y preguntas
y sus integraciones con Qdrant, embeddings y el LLM siguen pendientes.

The OCR component returns words with page-relative character offsets and boxes
in PDF points, relative to the top-left of the **unrotated crop box**. It first
uses the visible orientation and tries an alternative when confidence is low;
this heuristic is not guaranteed to choose correctly and does not handle
arbitrary skew. Byte, page and rendered-pixel limits, DPI, language and timeouts
are configurable in `.env.example`. The image includes only English Tesseract
language data; other languages require their corresponding packages.

El componente OCR devuelve palabras con offsets respecto al texto de su página y
cuadros en puntos PDF, relativos a la esquina superior izquierda del **área de
recorte sin rotar**. Primero usa la orientación visible y prueba una alternativa
si la confianza es baja; esta heurística no garantiza la elección correcta ni
corrige inclinaciones arbitrarias. Los límites de bytes, páginas y píxeles, DPI,
idioma y tiempos se configuran en `.env.example`. La imagen solo incluye los
datos de idioma inglés de Tesseract; otros idiomas necesitan sus paquetes.

For asynchronous callers, `OCRService.extract_async(pdf_bytes)`,
`extract_file_async(path)` and `extract_stream_async(stream)` use supervised
POSIX worker processes. FastAPI creates one shared service during startup; the
dependency `app.api.dependencies.get_ocr_service` retrieves it. Concurrent jobs,
queued jobs, queue wait, upload, document and shutdown timeouts are configurable
per application process. A started job remains supervised after caller
cancellation; shutdown terminates its process group after the configured grace
period. This is not a durable queue and cannot guarantee cleanup after a machine
or container is killed abruptly.

Para consumidores asíncronos, `OCRService.extract_async(pdf_bytes)`,
`extract_file_async(path)` y `extract_stream_async(stream)` usan procesos POSIX
supervisados. FastAPI crea un servicio compartido al iniciar; la dependencia
`app.api.dependencies.get_ocr_service` lo obtiene. Los trabajos simultáneos,
en espera y tiempos de cola, carga, documento y cierre son configurables por
proceso de aplicación. Un trabajo iniciado permanece supervisado si se cancela
quien lo espera; al cerrar, se termina su grupo de procesos tras el plazo de
espera. No es una cola persistente ni garantiza limpieza ante la caída abrupta
de la máquina o del contenedor.

### Extraction API / API de extracción

Send one PDF and a JSON array of name pairs as multipart form data. An empty list
is valid. / Envía un PDF y una lista JSON de parejas de nombres como multipart;
una lista vacía es válida.

```bash
curl --fail --silent --show-error http://localhost:8000/api/extract \
  -F 'pdf_file=@sample_pdfs/meeting_minutes.pdf;type=application/pdf' \
  -F 'names=[{"first_name":"Thomas","last_name":"Muller"}]'
```

The response follows `ExtractionResponse`: one `extracted_names` entry per
located occurrence and distinct `fuzzy_matches` pairs. Pages start at zero;
boxes use points relative to the top-left of the unrotated crop box. OCR is run
once per request. NER spans are mapped to OCR words on the same page; unsafe or
distant associations are omitted rather than assigned invented coordinates.
For a name split over adjacent lines, one enclosing rectangle may include blank
space. Full-name similarity ignores case and accents, removes apostrophes and
treats other punctuation as spaces. It uses `thefuzz.ratio` and returns scores
from 0 to 1; qualifying pairs retain deterministic input order.

La respuesta sigue `ExtractionResponse`: una entrada en `extracted_names` por
aparición localizada y parejas distintas en `fuzzy_matches`. Las páginas comienzan
en cero; los cuadros usan puntos relativos a la esquina superior izquierda del
área de recorte sin rotar. Se ejecuta OCR una sola vez por solicitud. Los spans
NER se relacionan con palabras OCR de la misma página; las asociaciones inseguras
o distantes se omiten en lugar de inventar coordenadas. Si un nombre ocupa dos
líneas contiguas, su único rectángulo puede incluir espacio en blanco. La similitud
de nombres completos ignora mayúsculas y acentos, elimina apóstrofos y trata la
demás puntuación como espacios. Usa `thefuzz.ratio`, devuelve puntuaciones de 0 a
1 y conserva un orden determinista de entrada.

`400`: invalid PDF or malformed `Content-Length`; `408`: upload timeout;
`413`: configured size or complexity limit; `422`: invalid multipart or names;
`503`: no capacity or dependency unavailable; `504`: processing timeout. Error
messages do not include document text. / Los mismos códigos indican PDF o
`Content-Length` inválido (`400`), timeout de carga (`408`), límite excedido
(`413`), multipart o nombres inválidos (`422`), falta de capacidad o dependencia
(`503`) y timeout de procesamiento (`504`). Los errores no incluyen el documento.

| Variable | Default | Scope / alcance |
|---|---:|---|
| `MAX_REQUEST_BYTES` | 22020096 (21 MiB) | Entire extraction request body, including multipart boundaries, part headers, fields and file / cuerpo completo, incluidos separadores multipart, cabeceras de partes, campos y archivo. |
| `MAX_UPLOAD_BYTES` | 20971520 (20 MiB) | PDF limit already enforced by OCR, separately from the request body / límite del PDF ya aplicado por OCR, independiente del cuerpo. |
| `MAX_NAMES_BYTES` | 65536 (64 KiB) | Raw text part and UTF-8 size of the decoded JSON field / parte de texto cruda y tamaño UTF-8 del campo JSON decodificado. |
| `MAX_NAMES_COUNT` | 1000 | Maximum number of pairs; an empty list is valid / máximo de parejas; una lista vacía es válida. |
| `MAX_NAME_CHARS` | 512 | Maximum characters per query component and detected span / máximo de caracteres por componente consultado y entidad detectada. |
| `FUZZY_THRESHOLD` | 0.90 | Full-name matching threshold, finite and within `0..1` / umbral de nombres completos, finito y entre `0..1`. |
| `EXTRACT_MAX_IN_FLIGHT` | 2 | Maximum in-flight extraction requests per application process / máximo de solicitudes simultáneas por proceso. |
| `EXTRACT_UPLOAD_TIMEOUT_SECONDS` | 30 | Total body-read deadline / plazo total para leer el cuerpo. |
| `MAX_EXTRACTED_NAMES` | 1000 | Maximum NER occurrences before location mapping / máximo de entidades NER antes de localizarlas. |
| `MAX_FUZZY_COMPARISONS` | 100000 | Maximum possible distinct extracted-name/query comparisons / máximo de comparaciones posibles entre nombres extraídos distintos y consultas. |
| `MAX_FUZZY_WORK_UNITS` | 100000000 | Maximum product of total normalized extracted-name and query lengths / máximo del producto de longitudes normalizadas de nombres extraídos y consultados. |

The body guard counts the actual ASGI chunks consumed by the application even
when `Content-Length` is missing or too small. It returns `413` when that limit
is exceeded, and `400` for malformed or repeated `Content-Length` headers. It
does not consume a body that the application never reads, and a response already
started cannot be changed to `413`. The endpoint consumes its form before
responding. A per-process admission limit is acquired before multipart parsing;
excess requests are rejected without reading their body. Uploads have a deadline,
and the multipart parser limits the PDF and text fields incrementally. These
controls are **not global** across HTTP workers or replicas. If a caller cancels,
already-started OCR/NER work remains supervised under its own service limits.

El límite del cuerpo cuenta los fragmentos ASGI que consume la aplicación aunque
falte `Content-Length` o declare menos bytes. Devuelve `413` al superar el límite
y `400` si `Content-Length` es inválido o está repetido. No consume cuerpos que
la aplicación no lee y no puede cambiar a `413` una respuesta ya iniciada. El
endpoint consume el formulario antes de responder. La admisión por proceso se
adquiere antes del parseo multipart; el exceso se rechaza sin leer el cuerpo.
Las cargas tienen plazo y el parser limita incrementalmente el PDF y los campos
de texto. Estos controles **no son globales** entre workers HTTP o réplicas. Si se
cancela la solicitud, el OCR/NER ya iniciado permanece supervisado bajo los
límites propios de cada servicio.

### Person detection / Detección de personas

`uv.lock` pins spaCy 3.8.18 and the official `en_core_web_sm` 3.8.0 wheel,
including its SHA-256. The wheel is downloaded from the official spaCy model
release during `uv sync` or image build; the build verifies it loads. Inference
requires no network or model download. `NER_MODEL` defaults to `en_core_web_sm`;
another model must already be installed and compatible. A missing model raises a
specific error instead of returning an empty result.

`NERService.detect_pages()` receives a list of page strings and returns a list of
`PersonSpan` lists in the same order, including empty pages. Offsets are Python
character positions local to each page, with an exclusive end; repeated
occurrences are kept. The async service executes spaCy in lazy, persistent POSIX
worker processes and reuses the model within each worker. The FastAPI lifespan
owns one service per application process. Synchronous callers may use
`SpacyPersonRecognizer` or `extract_names`; they must not run those blocking
functions directly on an async event loop.

`uv.lock` fija spaCy 3.8.18 y el wheel oficial `en_core_web_sm` 3.8.0 con su
SHA-256. El modelo se descarga desde la publicación oficial durante `uv sync` o la
construcción de la imagen, que comprueba su carga. La inferencia no necesita red
ni descarga el modelo. `NER_MODEL` vale `en_core_web_sm` por defecto; cualquier
otro modelo debe estar instalado y ser compatible. Si falta, se devuelve un
error específico, no una lista vacía engañosa.

`NERService.detect_pages()` recibe una lista de textos de páginas y devuelve
listas de `PersonSpan` en el mismo orden, incluidas páginas vacías. Los offsets
son posiciones de caracteres Python dentro de cada página y el final es exclusivo;
las apariciones repetidas se conservan. El servicio asíncrono ejecuta spaCy en
procesos POSIX persistentes y creados bajo demanda, reutilizando el modelo dentro
de cada proceso. El ciclo de vida de FastAPI mantiene un servicio por proceso de
aplicación. Para código síncrono existen `SpacyPersonRecognizer` y
`extract_names`; no deben ejecutarse directamente en el bucle asíncrono.

| Variable | Default | Meaning / significado |
|---|---:|---|
| `NER_MODEL` | `en_core_web_sm` | Installed model / modelo instalado. |
| `NER_MAX_PAGE_CHARS` | 100000 | Characters per page, at most 1000000 / caracteres por página, como máximo 1000000. |
| `NER_MAX_DOCUMENT_CHARS` | 1000000 | Characters across pages / caracteres sumados entre páginas. |
| `NER_MAX_CONCURRENT_JOBS` | 1 | Active NER workers per application process / procesos NER activos por proceso de aplicación. |
| `NER_MAX_QUEUED_JOBS` | 1 | Additional queued jobs per application process / trabajos adicionales en espera por proceso. |
| `NER_QUEUE_TIMEOUT_SECONDS` | 10 | Maximum queue wait / espera máxima en cola. |
| `NER_JOB_TIMEOUT_SECONDS` | 60 | Worker startup and inference deadline / plazo para iniciar el proceso y ejecutar inferencia. |
| `NER_SHUTDOWN_TIMEOUT_SECONDS` | 10 | Grace period for active work and worker exit / espera para trabajos activos y salida del proceso. |

The existing `MAX_PDF_PAGES` also bounds the number of pages sent to NER. Limits
are per process, not shared across HTTP workers or replicas. Extraction admission
also bounds the HTTP flow per process. On a timeout the worker is
terminated; a cancelled caller does not release an already-running job's capacity
until that work finishes or times out. Abrupt machine/container termination cannot
guarantee cleanup. The English small model predicts entity labels: filtering
`PERSON` does **not** guarantee all outputs are people or all people are found.
Sample OCR/NER observations and their limitations are in `docs/TEST_LOG.md`.

El límite existente `MAX_PDF_PAGES` también limita las páginas enviadas a NER.
Los límites son por proceso, no globales entre workers HTTP o réplicas; la admisión
de extracción también limita el flujo HTTP por proceso. Ante un timeout se termina el
proceso; si se cancela quien llama, un trabajo ya iniciado conserva su capacidad
hasta terminar o agotar su plazo. Una caída abrupta de la máquina o contenedor no
garantiza limpieza. El modelo pequeño de inglés predice etiquetas: filtrar
`PERSON` **no** garantiza que todos los resultados sean personas ni que encuentre
todos los nombres. Las observaciones y limitaciones están en `docs/TEST_LOG.md`.

### Smoke checks without a UI / Verificaciones reales sin interfaz

These optional checks use the current code, real Tesseract and spaCy, and the
repository's `sample_pdfs/meeting_minutes.pdf`; there are no mocks. They require
Docker Engine. Build a dedicated local image once, and rebuild it when the
Dockerfile or dependencies change. Building may require network access, but does
not start or restart the existing Compose services.

Estas verificaciones opcionales utilizan el código actual, Tesseract y spaCy
reales, y `sample_pdfs/meeting_minutes.pdf`; no usan mocks. Requieren Docker
Engine. Construye una imagen local dedicada y vuelve a construirla cuando cambien
el Dockerfile o las dependencias. La construcción puede necesitar red, pero no
inicia ni reinicia los servicios Compose existentes.

```bash
# From the repository root / Desde la raíz del repositorio:
docker build -t document-intelligence-smoke:local .

# One check at a time, or both / Una verificación a la vez, o ambas:
bash scripts/smoke/ejecutar.sh componentes
bash scripts/smoke/ejecutar.sh http
bash scripts/smoke/ejecutar.sh todo
```

`componentes` checks OCR word offsets, NER spans, page bounds and fuzzy matching
directly. `http` sends real HTTP requests to Uvicorn in the temporary container:
it checks the sample PDF, an empty name list, invalid inputs and the center of a
box in an **in-memory synthetic**, cropped and rotated scan. The scripts return
nonzero on failure. They expect the known names in the sample; counts and model
predictions may vary across environments.

`componentes` comprueba directamente offsets OCR, spans NER, límites de página
y fuzzy. `http` envía solicitudes HTTP reales a Uvicorn dentro del contenedor
temporal: comprueba el PDF de ejemplo, una lista vacía, entradas inválidas y el
centro de un cuadro en un escaneo **sintético en memoria**, recortado y rotado.
Los scripts devuelven un código distinto de cero si falla una condición. Esperan
los nombres conocidos de la muestra; los recuentos y predicciones pueden variar.

Each check uses disposable containers with `--network none`; code, scripts and
sample PDFs are mounted read-only. HTTP uses loopback inside the container and
publishes no host ports. Only the HTTP container created by the runner is stopped;
cleanup cannot be guaranteed after an abrupt termination. The container uses its
image's configuration, not the host's Compose `.env`. To use another compatible
local image, set `SMOKE_IMAGE`, for example:

Cada verificación usa contenedores temporales con `--network none`; código,
scripts y PDF de muestra se montan en solo lectura. HTTP usa loopback dentro del
contenedor y no publica puertos en el host. Solo se detiene el contenedor HTTP
creado por el ejecutor; ante una terminación abrupta no puede garantizarse la
limpieza. El contenedor usa la configuración de su imagen, no el `.env` de
Compose del host. Para usar otra imagen local compatible, indica `SMOKE_IMAGE`:

```bash
SMOKE_IMAGE=another-local-image:tag bash scripts/smoke/ejecutar.sh todo
```

The field-size check expects `MAX_NAMES_BYTES` at most 1 MiB and enough request
capacity for the sample PDF and that field; the image built above uses compatible
defaults. These checks do not test Compose, Qdrant, production traffic or general
model accuracy, and do not replace pytest or human visual review (MT-002).

La comprobación del tamaño del campo requiere `MAX_NAMES_BYTES` de hasta 1 MiB y
espacio suficiente en la solicitud para el PDF y ese campo; la imagen construida
arriba usa valores compatibles. Estas verificaciones no prueban Compose, Qdrant,
tráfico de producción ni la precisión general del modelo, y no sustituyen pytest
ni la revisión visual humana (MT-002).

Test commands, observed results and incident resolutions are recorded in
[`docs/TEST_LOG.md`](docs/TEST_LOG.md); human review results are recorded in
[`docs/MANUAL_TEST_LOG.md`](docs/MANUAL_TEST_LOG.md); engineering decisions are in
[`DECISIONS.md`](DECISIONS.md). Open OCR review findings and proposed fixes are
tracked in [`docs/OCR_REVIEW_PLAN.md`](docs/OCR_REVIEW_PLAN.md).

Los comandos de prueba, resultados observados y resoluciones de incidencias se
registran en [`docs/TEST_LOG.md`](docs/TEST_LOG.md); las revisiones humanas, en
[`docs/MANUAL_TEST_LOG.md`](docs/MANUAL_TEST_LOG.md); las decisiones de ingeniería
están en [`DECISIONS.md`](DECISIONS.md). Los hallazgos abiertos del OCR y las
correcciones propuestas se siguen en [`docs/OCR_REVIEW_PLAN.md`](docs/OCR_REVIEW_PLAN.md).
