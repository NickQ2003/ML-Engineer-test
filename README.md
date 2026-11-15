# ML Engineer Take-Home — Document Intelligence Service

Build, from scratch, a small production-quality service that extracts person
names (with their locations) from scanned PDFs, fuzzy-matches them against a
provided list, and answers questions about ingested documents using RAG
(Retrieval-Augmented Generation).

This repository is an **empty skeleton**. There is no working code to fix — you
implement the whole thing. We care as much about *how* you build it (design,
tests, engineering practices, decisions) as about whether it runs.

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
uv run python -m spacy download en_core_web_sm
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
uv run python -m spacy download en_core_web_sm
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
