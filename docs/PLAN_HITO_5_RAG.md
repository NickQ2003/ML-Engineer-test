# Hito 5 — Preguntas y respuestas basadas en documentos

## Objetivo y estado

Implementar `POST /api/ask` sobre los documentos ingeridos: recibir una pregunta,
generar su embedding, recuperar fragmentos elegibles, consultar un LLM con ese
contexto y devolver una respuesta fundamentada con fuentes. El contrato fijo es
`RAGRequest(question: str)` y `RAGResponse(answer: str, sources: list[str])` en
`app/models/schemas.py`; no modificarlo. El endpoint sigue pendiente: la base
agregada en este hito inicial **no lo implementa ni activa**.

Hito 4 está implementado y probado en el alcance registrado, no implica que la
precisión esté garantizada para cualquier documento. MT-002 fue revisada por el
usuario sobre el ejemplo; consultar `docs/MANUAL_TEST_LOG.md`. Mantener el estado
del repositorio existente, no hacer commits sin autorización y no modificar ni
reiniciar los servicios o volúmenes Compose existentes durante las pruebas.

## Alcance y exclusiones

- Incluye preguntas JSON, búsqueda semántica, selección segura de fuentes,
  integración de LLM por adaptador configurable, límites, errores, pruebas y
  documentación. Usar exclusivamente ingestas `completed` del ledger correcto.
- No incluye UI, autenticación multiusuario, Celery, streaming, filtros por
  documento en el contrato, borrado de documentos ni deduplicación de ingestas.
- El servicio actual no ofrece aislamiento de documentos por usuario: no exponer
  `/api/ask` a usuarios no confiables como si fuera multiusuario. Las preguntas y
  fragmentos son sensibles. Enviar texto a un LLM externo requiere una decisión
  explícita del usuario sobre proveedor, datos, credenciales y costes. No enviar
  datos reales ni descargar modelos nuevos sin autorización.

## Situación existente a respetar

- `app/services/ingestion_service.py` escribe payloads con `kind`, versión,
  `ledger_id`, `ingestion_id`, `document_id`, `chunk_id`, `source_file`,
  `page_number` (física, base 0), `chunk_index`, texto y revisión del modelo.
- `app/services/ingestion_vectors.py` protege la colección mediante un manifiesto
  de modelo/ledger y un lock. No crear, adoptar, borrar ni modificar colecciones
  durante una consulta. Un punto puede existir antes de que su ingesta complete:
  los filtros de Qdrant por sí solos no prueban que sea elegible.
- `IngestionLedger.get()` permite comprobar estado e identidad; SQLite y Qdrant
  no forman una transacción. Diseñar y probar las carreras con escrituras y
  limpieza, y fallar cerrado ante identidad dudosa o ledger no disponible.
- El modelo de embeddings está fijado en `app/core/embedding_model.py`.
  `EmbeddingService.prepare()` solo acepta páginas para ingesta; el helper
  `get_query_embedding()` es síncrono, no debe bloquear el event loop y no
  resuelve por sí solo concurrencia, timeout ni cancelación.
- Cada ingesta crea identidad propia aunque el PDF sea idéntico. No agrupar
  automáticamente ni usar el hash como identificador de una fuente única.

## Decisiones de diseño propuestas

1. Mantener router fino y `AskService` con interfaces inyectables para embeddings,
   recuperación, ledger y generación; compartir los recursos de lifespan y cerrar
   clientes en shutdown. Nada de singleton o HTTP real en tests unitarios.
2. Normalizar la pregunta con `strip`, rechazar vacío y longitudes superiores a
   2000 caracteres sin registrar el texto; no truncar silenciosamente. Comprobar
   también el límite de tokens del modelo y devolver error de entrada explícito.
3. Extender el worker de embeddings supervisado con una operación de consulta,
   usando el mismo snapshot y normalización. Definir admisión compartida con
   ingesta, límites de cola, timeout y cancelación; evitar cargas simultáneas
   duplicadas o que una petición mate el trabajo de otra.
4. Buscar solo chunks con versión, ledger y modelo compatibles; verificar
   manifiesto sin crear recursos. Validar payload, vector y score. Comprobar en
   el ledger que cada `ingestion_id` es `completed` y coincide en documento,
   colección, endpoint, modelo y firma. No incluir el manifiesto ni fragmentos
   de intentos `writing`, fallidos o con limpieza pendiente. Decidir cómo
   coordinar la lectura con lock existente y probar carreras; no confiar en una
   validación efectuada antes de una modificación concurrente.
5. La búsqueda debe tener límites configurables de candidatos, top-k y tamaño de
   contexto. Como candidatos inelegibles pueden ocupar los primeros puestos,
   recuperar por lotes o sobrebuscar hasta un límite explícito; no afirmar que
   se obtuvo el top-k global si se agotó ese límite. Definir desempate estable.
   No usar un umbral de similitud como garantía de relevancia sin calibrarlo.
6. Formatear fuentes deterministas y legibles como `nombre — página N` (N base
   1 para presentación), con identificadores internos cuando sea necesario para
   distinguir ingestas. Nunca inventar fuentes ni incluir fragmentos rechazados.
   El modelo puede citar solo IDs asignados a fragmentos seleccionados; validar
   IDs devueltos. El formato definitivo debe documentarse y probarse.
7. Sin contexto elegible, responder explícitamente que no hay evidencia y
   `sources=[]`, sin llamar al LLM. Para contexto irrelevante, instruir al modelo
   a abstenerse; no prometer que esa instrucción elimina alucinaciones. Texto de
   documentos es dato no confiable, no instrucciones del sistema.
8. Adaptador LLM con configuración y secreto protegido, límites de respuesta,
   timeout, retries acotados solo para errores transitorios y sin registrar
   prompts, respuestas o credenciales. Proveedor/modelo, política de envío y
   configuración de producción requieren acuerdo previo; usar fake en pruebas
   hasta entonces. Errores de dependencia deben ser 503/504, nunca éxito falso.

## Orden de implementación para GPT-6.1 Terra Medium

### 0. Contratos y pruebas de base — iniciado

- `app/services/ask_input.py` y `tests/unit/test_ask_input.py`: normalización de
  pregunta aislada, sin cambiar el contrato ni conectar el endpoint. Ejecutar sus
  pruebas antes de seguir. Las validaciones de tokens se hacen en el worker.
- Confirmar formato de fuentes y decisión de proveedor/privacidad antes de
  integrar una llamada externa. Si no hay decisión, continuar solo con interfaces
  y adaptadores fake; no afirmar que el hito está terminado.

### 1. Embedding de consulta

- **Avance inicial:** `EmbeddingService.embed_query()` reutiliza el worker
  supervisado y el modelo fijado, normaliza la pregunta y rechaza exceso de
  tokens sin truncar. La admisión actual es inmediata: si ingesta o consulta
  ocupa el worker, la siguiente petición se rechaza; no hay cola. Una consulta
  no cancela el trabajo que ya ocupa el worker. Todavía falta integrar el servicio
  con `/api/ask`, definir la política HTTP de saturación y completar los tests de
  extremo a extremo del hito.
- Extender el protocolo `embedding_worker.py` y `embedding_async.py` de forma
  retrocompatible; preservar `prepare()`, cierre y errores de ingesta.
- Tests: misma revisión/dimensión y norma finita, vacío, exceso de tokens sin
  truncar, concurrencia con ingesta, saturación, timeout, cancelación, limpieza y
  secretos excluidos. Al menos una prueba con modelo real offline.

### 2. Recuperación y elegibilidad

- **Avance inicial:** `app/services/ask_retrieval.py` añade lectura acotada y
  comprueba manifiesto e identidad y estado de cada ingesta bajo el mismo lock
  de colección usado por escritores cooperantes. Descarta estados incompletos y
  falla cerrado ante datos inconsistentes o si se agota el presupuesto de
  candidatos antes de llenar el resultado. No crea colecciones. Sus pruebas
  actuales usan Qdrant y ledger simulados; falta validación integrada con
  servicios reales aislados y conexión a HTTP. No es un mecanismo multiusuario.
- Crear adaptador de lectura, interfaces y DTO internos; reutilizar validación de
  manifiesto sin efectos de escritura. Resolver explícitamente colección ausente:
  distinguida de colección incompatible y ledger inaccesible, sin crearla.
- Tests con fakes para selección, payload malformado, identidad inconsistente,
  documentos duplicados, estados incompletos, candidatos rechazados, límite de
  búsqueda, carreras, timeout y fallos cerrados. Añadir smoke con Qdrant y ledger
  temporales propios, no con los existentes.

### 3. Respuesta fundamentada

- **Avance inicial:** `app/services/ask_service.py` define interfaces internas
  para embeddings, recuperación y generación, limita contexto sin truncar
  fragmentos, valida IDs de citas y se abstiene sin evidencia o citas. Las
  pruebas usan fakes. El recuperador de producción aún no existe: su contrato
  exige verificar elegibilidad en el ledger, no se debe conectar un recuperador
  que no lo haga. Aún no hay endpoint ni proveedores reales.
- Implementar servicio con límite total de contexto, IDs de cita y abstención;
  probar con generador fake: sin evidencia no invoca LLM, fuentes pertenecen a
  fragmentos elegibles, citas inválidas son rechazadas y texto hostil no se trata
  como instrucción confiable. Aislar errores y cancelaciones.
- Añadir adaptador real únicamente tras decisión del usuario sobre proveedor y
  transferencia de datos. Documentar dependencia, modelo y coste; prueba real
  solo con consentimiento y datos sintéticos, indicando si no pudo ejecutarse.

### 4. HTTP y ciclo de vida

- Añadir `POST /api/ask` al router existente con `RAGRequest`/`RAGResponse`,
  límites de cuerpo y concurrencia, inyección y errores seguros. Mantener
  `/api/extract`, `/api/ingest` y sus respuestas sin cambios.
- Pruebas HTTP: JSON inválido, espacios, exceso de caracteres/tokens, respuesta
  sin contexto, fuentes, saturación, timeout, caída de cada dependencia,
  cancelación y no exposición de textos o secretos en logs/errores.

### 5. Validación y entrega

- Ejecutar batería completa y smoke aislado: ingesta real de PDF sintético,
  recuperación real con embeddings/Qdrant, generación fake identificada como
  tal; generación real solo si está autorizada. Comprobar ingesta interrumpida,
  colección incompatible y ausencia de efectos en Compose existente.
- Actualizar README, DECISIONS y TEST_LOG con configuración, riesgos, resultados
  reales versus simulados; no marcar calidad RAG validada sin corpus anotado.

## Criterios de aceptación

Contrato intacto; ingestas no completadas nunca llegan al LLM ni a fuentes;
sin evidencia no se inventa respuesta; fuentes corresponden al contexto elegido;
preguntas y contexto acotados sin truncado silencioso; timeouts y errores
controlados; ningún dato sensible en logs; recursos cerrados; extracción e
ingesta sin regresiones; pruebas automatizadas y smoke aislado reproducibles.
La calidad semántica y resistencia a prompt injection requieren evaluación
adicional y no quedan garantizadas por estas pruebas.
