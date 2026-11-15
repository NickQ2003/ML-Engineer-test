# Bitácora de pruebas y ejecuciones

Este documento versionado registra las comprobaciones realizadas durante el
desarrollo, sus resultados y la resolución de problemas. Las salidas mostradas
son fragmentos relevantes, no necesariamente la salida completa. Los valores de
tiempo, tamaño o identificadores pueden cambiar entre ejecuciones.

Al añadir una entrada, registrar fecha, entorno, comando, salida observada,
resultado, limitaciones y, cuando exista un error, su diagnóstico y resolución.
No incluir claves, credenciales, datos personales ni contenido sensible de PDF.

## 2026-11-14 — Hito 1: infraestructura inicial

**Entorno:** macOS ARM64, Docker Desktop con contenedores Linux ARM64, Python
3.12 en el contenedor. Las pruebas locales de Python se ejecutaron en un entorno
temporal con Python 3.12.16. El motor Docker reportó versión 28.3.3 y Compose
versión 2.39.2. El proyecto se encuentra en la rama de desarrollo.

### Configuración y arranque de contenedores

```bash
docker compose config
docker compose up --build -d
docker compose ps
```

**Resultado observado:** Compose procesó la configuración y construyó la imagen;
`app` y `qdrant` aparecieron con estado `Up`. Se creó el volumen
`ml-engineer-test_qdrant_data`. La comprobación del archivo de configuración no
garantiza por sí sola que los servicios estén listos para aceptar solicitudes.

### Salud de la API

```bash
curl --fail --silent --show-error http://localhost:8000/health
```

**Respuesta observada:**

```json
{"status":"ok"}
```

**Resultado:** correcto. Esta ruta es una comprobación de vida de la API; no
comprueba la conexión de la aplicación con Qdrant ni con un LLM.

### Disponibilidad de Qdrant desde la red interna

```bash
docker compose exec -T app python -c \
  "import urllib.request; print(urllib.request.urlopen('http://qdrant:6333/readyz', timeout=5).read().decode())"
```

**Respuesta observada:**

```text
all shards are ready
```

**Resultado:** correcto para conectividad de red y disponibilidad de Qdrant.
No verifica todavía el adaptador de la aplicación ni la persistencia de datos.

### Pruebas unitarias iniciales en macOS

Se ejecutaron en un entorno temporal con las dependencias mínimas de prueba,
no con todas las dependencias del proyecto:

```bash
python -m pytest tests/unit
```

**Salida relevante observada:**

```text
2 passed, 1 warning
StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
```

**Resultado:** las pruebas pasaron, con una advertencia de compatibilidad de
Starlette. No se aplicó una corrección específica a esa advertencia. En la
ejecución posterior dentro de Linux con las dependencias de la imagen no apareció;
esos entornos no tenían exactamente el mismo conjunto de dependencias.

### Incidencia: imagen demasiado grande por CUDA

En la primera construcción se detectaron paquetes CUDA/NVIDIA en la salida de
`uv sync`. La construcción terminó correctamente, pero el tamaño era excesivo
para el alcance CPU del proyecto:

```bash
docker image inspect ml-engineer-test-app:latest --format '{{.Size}} bytes'
```

**Resultado observado antes del cambio:**

```text
10691090779 bytes
```

**Diagnóstico:** PyTorch se resolvió con dependencias CUDA en Linux. Además, la
imagen conservaba la caché de `uv` y una capa adicional causada por cambiar
recursivamente el propietario del entorno virtual. No se necesitaba GPU.

**Corrección:** se declaró `torch` como dependencia directa y se configuró en
`pyproject.toml` el índice oficial de PyTorch para CPU en Linux. Se regeneró
`uv.lock`. En `Dockerfile` se deshabilitó la caché persistente de `uv` y se
eliminó el `chown -R` del entorno virtual, conservando el usuario sin privilegios.
La decisión y sus compromisos se documentan en [DECISIONS.md](../DECISIONS.md).

Durante el ajuste inicial, configurar el índice solo para la dependencia
transitiva no cambió la resolución: seguían apareciendo paquetes NVIDIA. Tras
declarar `torch` como dependencia directa y volver a ejecutar `uv lock`, el
resultado incluyó `torch 2.15.0+cpu` para Linux y eliminó esas dependencias.

### Validación posterior a la corrección de CUDA

Se regeneró y verificó el archivo de bloqueo con `uv` 0.13.8. En el equipo de
desarrollo se utilizó una instalación temporal de `uv`, no una global:

```bash
uv lock --python 3.12
uv lock --check --python 3.12
```

**Salida relevante de la comprobación:**

```text
Using CPython 3.12.16
Resolved 106 packages
```

Se reconstruyó y se midió la imagen con la misma métrica anterior:

```bash
docker compose up --build -d
docker image inspect ml-engineer-test-app:latest --format '{{.Size}} bytes'
```

**Resultado observado después del cambio:**

```text
629766551 bytes
```

La reducción observada corresponde al conjunto de cambios en dependencias y
capas, no únicamente a eliminar CUDA. La imagen y la caché anteriores no se
eliminaron automáticamente y pueden seguir ocupando espacio local.

Se verificaron PyTorch y la importación de `sentence-transformers` dentro de la
imagen, sin descargar ni ejecutar un modelo de embeddings:

```bash
docker compose exec -T app python -c \
  "import torch; import sentence_transformers; print('torch:', torch.__version__); print('CUDA build:', torch.version.cuda); print('tensor:', (torch.tensor([2.0]) * 3).item()); print('sentence-transformers:', sentence_transformers.__version__); assert torch.version.cuda is None"
```

**Salida observada:**

```text
torch: 2.15.0+cpu
CUDA build: None
tensor: 6.0
sentence-transformers: 6.3.0
```

El usuario de ejecución también se comprobó con `docker compose exec -T app id`:

```text
uid=10001(appuser) gid=10001(appuser) groups=10001(appuser)
```

La API volvió a responder `{"status":"ok"}` después de reconstruir la imagen.

### Pruebas unitarias dentro de Linux

Se usó un contenedor temporal basado en la imagen construida, con el directorio
de pruebas montado en modo solo lectura. `pytest` se instaló temporalmente en
ese contenedor; el comando necesita acceso a la red:

```bash
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  --entrypoint /bin/sh \
  ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit'
```

**Salida relevante observada:**

```text
tests/unit/test_config.py .
tests/unit/test_cpu_dependencies.py .
tests/unit/test_health.py .
3 passed in 2.82s
```

**Resultado:** correcto en Linux ARM64. Se verificaron configuración, salud y
la variante CPU de PyTorch. Los tiempos pueden variar.

### Incidencia de publicación en GitHub

```bash
git push -u origin feature/document-intelligence
```

**Respuesta observada:** GitHub devolvió HTTP `403` por falta de permiso de
escritura de la cuenta autenticada en el repositorio original.

**Diagnóstico:** la cuenta autenticada no tiene permiso de escritura en el
repositorio original. La alternativa elegida fue crear un fork propio y usarlo
para publicar la rama, conservando el repositorio original como `upstream`.

Se volvió a intentar sin interacción de terminal:

```bash
GIT_TERMINAL_PROMPT=0 git push -u origin feature/document-intelligence
```

**Respuesta observada:** el servidor volvió a rechazar el acceso con HTTP 403
para la cuenta `NickQ2003`. La rama no se publicó; no se hicieron cambios en el
remoto. Los cambios locales sin commit tampoco se incluyeron en el intento.

**Resolución:** se creó el fork con:

```bash
gh repo fork anvasquezre-cuesta/ML-Engineer-test --clone=false
```

**Respuesta observada:** `https://github.com/NickQ2003/ML-Engineer-test`.
Se configuró ese fork como `origin` y el repositorio original como `upstream`.
Luego se publicó la rama en el fork:

```bash
GIT_TERMINAL_PROMPT=0 git push -u origin feature/document-intelligence
```

**Resultado observado:** se creó la rama remota
`origin/feature/document-intelligence` y la rama local quedó configurada para
seguirla. Esto resuelve la publicación en el fork, no concede permiso de
escritura en el repositorio original. El pull request aún no se ha abierto.

### Comprobaciones pendientes

- Escribir datos en Qdrant, reiniciar el contenedor y comprobar su persistencia.
- Probar la imagen en Linux x86_64; la ejecución anterior fue en Linux ARM64.
- Comprobar el adaptador Qdrant, modelos reales y endpoints de extracción y RAG
  cuando se implementen; no forman parte de las pruebas ya ejecutadas.

## 2026-11-14 — Hito 2: OCR y coordenadas

**Entorno:** Docker Linux ARM64, Python 3.12.16, Tesseract y datos de inglés
incluidos en la imagen. Los archivos de aplicación y pruebas se montaron desde
el árbol de trabajo para verificar los cambios antes de reconstruir la imagen.

### Pruebas iniciales y cierre de recursos

En la primera ejecución de la batería OCR, la salida relevante fue:

```text
1 failed, 18 passed
test_wraps_ocr_failures_and_closes_images: DID NOT RAISE ValueError
```

**Diagnóstico:** el administrador de contexto de una imagen Pillow en memoria
no cerraba el recurso de la manera que exigía la prueba ante un error de OCR.
**Solución:** usar `contextlib.closing` para liberar explícitamente las imágenes;
la prueba posterior también verifica el cierre del documento PDF al fallar OCR.

### Prueba de rotación y corrección

Se ejecutó OCR real sobre PDF sintéticos escaneados con diferentes rotaciones.
Antes de la corrección, con los mismos caracteres de prueba, la salida fue:

```text
rotation 0 text 'ALICE SMITH'
rotation 90 text 'ALICE SMITH'
rotation 180 text 'HLINS S0N1V'
rotation 270 text 'HLINS S0N1V'
```

**Diagnóstico:** el renderizado respetaba la rotación del PDF, pero Tesseract
podía leer el texto invertido. **Solución:** normalizar la orientación usando
los metadatos del PDF antes de OCR y transformar los cuadros de vuelta a puntos
del área de recorte sin rotar. No es una corrección general de inclinación ni de
escaneos invertidos sin metadatos.

### OCR real sobre PDF de ejemplo

```bash
docker run --rm \
  -v "$PWD/app:/app/app:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  --entrypoint python ml-engineer-test-app:latest \
  -c 'from pathlib import Path; from app.services.ocr_service import OCRService; svc = OCRService(); [(print(path.name, "pages:", len(doc.pages), "words:", [len(page.words) for page in doc.pages], "dimensions:", [(round(page.width), round(page.height)) for page in doc.pages])) for path in sorted(Path("/samples").glob("*.pdf")) for doc in [svc.extract_file(path)]]'
```

**Salida observada antes del ajuste de orientación:**

```text
company_memo.pdf pages: 1 words: [143] dimensions: [(612, 792)]
meeting_minutes.pdf pages: 2 words: [149, 134] dimensions: [(612, 792), (612, 792)]
research_report.pdf pages: 2 words: [129, 134] dimensions: [(612, 792), (612, 792)]
```

Los tres documentos se volvieron a procesar en la batería posterior. La cantidad
de palabras depende del motor y sus datos; no demuestra por sí sola precisión de
NER ni de cuadros de nombres.

### Batería final: unitarias e integración OCR

```bash
docker run --rm \
  -v "$PWD/app:/app/app:ro" \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration/test_ocr_real.py'
```

**Salida observada:**

```text
collected 33 items
tests/unit/test_config.py .
tests/unit/test_cpu_dependencies.py .
tests/unit/test_health.py .
tests/unit/test_ocr_service.py ...................
tests/integration/test_ocr_real.py ...........
33 passed in 6.86s
```

Se probaron las tres muestras y PDF sintéticos escaneados con las cuatro
rotaciones y con/sin recorte. Las pruebas sintéticas verifican que el centro del
cuadro OCR de una palabra coincida con su ubicación esperada; no garantizan
precisión perfecta de todos los bordes o de cualquier documento. También se
probaron límites de entrada, PDF inválidos y cifrados y limpieza ante error.

Después se reconstruyó la imagen y se verificó el servicio:

```bash
docker compose up --build -d
curl --fail --silent --show-error http://localhost:8000/health
```

**Resultado observado:** ambos contenedores iniciaron y `/health` respondió
`{"status":"ok"}`. Se repitió la batería usando el código incluido en la imagen
reconstruida, sin montar `app/` desde el host:

```bash
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration/test_ocr_real.py'
```

**Salida relevante:** `33 passed in 7.32s`.

**Pendientes:** implementar validación HTTP y el endpoint de extracción, NER,
un límite de píxeles renderizados, pruebas en x86_64 y persistencia de Qdrant.
El idioma instalado es inglés; otros idiomas requieren datos adicionales.

## 2026-11-15 — Hito 2: ejecución asíncrona del OCR

**Objetivo:** no bloquear el bucle de eventos mientras se procesan PDF y limitar
el número de trabajos OCR simultáneos sin cambiar el contrato HTTP de la prueba.
**Entorno:** imagen Docker Linux ARM64 reconstruida, Python 3.12.16, Tesseract
en inglés y pytest 9.2.0 instalado en el contenedor temporal de pruebas.

**Implementación:** `extract_async` y `extract_file_async` envían el trabajo
bloqueante a procesos de AnyIO, evitando ejecución paralela de PyMuPDF en hilos.
`get_ocr_service()` permite reutilizar el servicio y su limitador. El valor
`OCR_MAX_CONCURRENT_JOBS` es configurable y predetermina 2 por proceso de la
aplicación. El endpoint HTTP todavía no está implementado.

### Construcción y comprobación de configuración

```bash
uv lock --python 3.12
docker compose up --build -d
```

**Resultado observado:** `uv` resolvió 106 paquetes; Docker construyó la imagen
y arrancó la aplicación. Se agregó `anyio` como dependencia directa y se actualizó
`uv.lock`.

Se comprobó además la coherencia del bloqueo con `uv lock --check --python 3.12`
(106 paquetes resueltos) y que `docker compose config --quiet` terminó sin error.
Tras el arranque, `docker compose ps` mostró `app` y `qdrant` activos; `curl
--fail --silent --show-error http://localhost:8000/health` devolvió
`{"status":"ok"}`. Dentro de la aplicación se comprobó:

```bash
docker compose exec -T app python -c "from app.core.config import get_settings; from app.services.ocr_service import get_ocr_service; print('OCR_MAX_CONCURRENT_JOBS:', get_settings().ocr_max_concurrent_jobs); print('shared_service:', get_ocr_service() is get_ocr_service())"
```

**Salida observada:**

```text
OCR_MAX_CONCURRENT_JOBS: 2
shared_service: True
```

### Pruebas unitarias e integración en la imagen reconstruida

```bash
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration'
```

**Salida relevante observada:**

```text
collected 44 items
tests/unit/test_config.py .
tests/unit/test_cpu_dependencies.py .
tests/unit/test_health.py .
tests/unit/test_ocr_async.py ........
tests/unit/test_ocr_service.py ...................
tests/integration/test_async_http.py .
tests/integration/test_ocr_real.py .............
44 passed in 16.76s
```

Se verificaron, entre otros: ejecución en un proceso distinto, bucle de eventos
no bloqueado, límite de concurrencia, errores de validación, rechazo de archivos
grandes antes de enviarlos al proceso, cancelación de una tarea en espera,
finalización de un trabajo ya iniciado ante cancelación con AnyIO y OCR real por
las interfaces de bytes y de archivo. Una prueba usa una ruta OCR simulada y la
ruta real `/health` para comprobar que esta última siga respondiendo; no prueba
el futuro endpoint `/api/extract`.

**Limitaciones:** el límite se aplica por proceso de aplicación, no globalmente;
no limita el número de solicitudes en cola ni la memoria de cargas ya recibidas.
No es una cola persistente: si el proceso termina, no hay garantía de conservar
el trabajo. Falta probar los endpoints reales cuando se implementen y validar
el comportamiento en Linux x86_64.

## 2026-11-15 — Revisión de OCR y concurrencia

**Estado:** revisión completada; las correcciones siguen pendientes. El plan y
los criterios de aceptación están en [OCR_REVIEW_PLAN.md](OCR_REVIEW_PLAN.md).
Se repitió el comando de la batería anterior en la imagen construida y se observó
`44 passed in 17.34s`. Las reproducciones siguientes usaron contenedores
temporales Linux ARM64; no se cambiaron los PDF de ejemplo.

### Página enorme: detección sin reservar la imagen

Se interceptó `get_pixmap()` para observar la reserva que se intentaría hacer,
sin asignar realmente los más de 2 GB estimados para una copia RGB:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
import pymupdf
from app.services.ocr_service import OCRService, OCRProcessingError
with pymupdf.open() as doc:
    doc.new_page(width=10000, height=10000)
    data=doc.tobytes()
original=pymupdf.Page.get_pixmap
def intercepted(page, *args, **kwargs):
    scale=200/72
    print('Input bytes:',len(data))
    print('Approximate rendered pixels:',round(page.rect.width*scale)*round(page.rect.height*scale))
    print('Approximate RGB bytes for one copy:',3*round(page.rect.width*scale)*round(page.rect.height*scale))
    raise RuntimeError('render intercepted before allocation')
pymupdf.Page.get_pixmap=intercepted
try:
    OCRService().extract(data)
except OCRProcessingError as exc:
    print('Reached rendering instead of rejecting:',str(exc))
finally:
    pymupdf.Page.get_pixmap=original
PY
```

**Salida observada:**

```text
Input bytes: 522
Approximate rendered pixels: 771617284
Approximate RGB bytes for one copy: 2314851852
Reached rendering instead of rejecting: OCR failed on page 0
```

**Resultado:** incorrecto para una protección de recursos suficiente. Se abrió
R-01; no se ha aplicado aún un límite de píxeles.

### Rotación de metadatos que corrige un escaneo girado

Se construyó un PDF sintético con imagen físicamente girada y rotación de
metadatos que la muestra derecha, y se comparó OCR real sobre la vista con el
resultado del servicio:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
from io import BytesIO
from PIL import Image
import pymupdf, pytesseract
from app.services.ocr_service import OCRService
with pymupdf.open() as d:
    p=d.new_page(width=400,height=300)
    p.insert_text((40,100),'ALICE SMITH',fontsize=32)
    p.insert_text((40,150),'JOHN ANDERSON',fontsize=32)
    pix=p.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
    im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    sideways=im.rotate(90,expand=True)
    b=BytesIO(); sideways.save(b,format='PNG')
    with pymupdf.open() as doc:
        sp=doc.new_page(width=300,height=400)
        sp.insert_image(sp.rect,stream=b.getvalue())
        sp.set_rotation(90)
        visible=sp.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
        visible_img=Image.frombytes('RGB',(visible.width,visible.height),visible.samples)
        print('Displayed orientation OCR:',repr(pytesseract.image_to_string(visible_img,lang='eng').strip()))
        print('Service OCR:',repr(OCRService().extract(doc.tobytes()).text))
        visible_img.close()
    im.close(); sideways.close()
PY
```

**Salida observada:**

```text
Displayed orientation OCR: 'ALICE SMITH\nJOHN ANDERSON'
Service OCR: 'NOSdYsAdNV NHOPr\nHLINWIS S0NIV'
```

**Resultado:** incorrecto. Se abrió R-03; la normalización de rotación sigue
pendiente de corrección.

### Cancelación directa durante un subprocesso OCR

Se sustituyó temporalmente el ejecutable de Tesseract dentro del contenedor por
un proceso controlado de larga duración, conservando la comprobación real de
versión. El proceso simulado se detuvo antes de terminar la prueba:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" -v "$PWD/sample_pdfs:/samples:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
import asyncio, os, signal
from pathlib import Path
from app.services.ocr_service import OCRService
fake=Path('/tmp/review-fakebin'); fake.mkdir()
tesseract=fake/'tesseract'
tesseract.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then exec /usr/bin/tesseract --version; fi\necho $$ > /tmp/review-tesseract.pid\nexec sleep 30\n')
tesseract.chmod(0o755)
os.environ['PATH']=str(fake)+':'+os.environ['PATH']
async def main():
    marker=Path('/tmp/review-tesseract.pid')
    task=asyncio.create_task(OCRService().extract_file_async('/samples/company_memo.pdf'))
    async with asyncio.timeout(15):
        while not marker.exists(): await asyncio.sleep(.02)
    pid=int(marker.read_text())
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
    print('Tesseract survived:',Path(f'/proc/{pid}/status').exists())
    print('Tesseract-related temp files left:',len(list(Path('/tmp').glob('tess_*'))))
    try: os.kill(pid,signal.SIGKILL)
    except ProcessLookupError: pass
asyncio.run(main())
PY
```

**Salida observada:**

```text
Tesseract survived: True
Tesseract-related temp files left: 2
```

**Resultado:** incorrecto. Se abrió R-02; la prueba actual de cancelación AnyIO
no cubre esta cancelación directa. El contenedor temporal se eliminó al finalizar.

### Capacidad e inicialización concurrente

Con el único permiso del limitador ocupado, se lanzaron cuatro llamadas con
entradas instrumentadas de 1 MB:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
import asyncio
from app.core.config import Settings
from app.services.ocr_service import OCRService
calls=[]
class TrackedBytes(bytes):
    def __reduce_ex__(self, protocol):
        calls.append(len(self))
        return bytes, (bytes(self),)
async def main():
    service=OCRService(Settings(ocr_max_concurrent_jobs=1))
    async with service._process_limiter:
        tasks=[asyncio.create_task(service.extract_async(TrackedBytes(b'x'*1_000_000))) for _ in range(4)]
        await asyncio.sleep(.1)
        print('jobs waiting with no capacity: 4')
        print('payloads already serialized:',len(calls))
        print('total payload bytes already serialized:',sum(calls))
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
asyncio.run(main())
PY
```

**Salida observada:**

```text
jobs waiting with no capacity: 4
payloads already serialized: 4
total payload bytes already serialized: 4000000
```

Se llamó también al proveedor desde cuatro hilos usando una barrera en una
implementación simulada del constructor:

```bash
docker run --rm -i -v "$PWD/app:/app/app:ro" --entrypoint python ml-engineer-test-app:latest - <<'PY'
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import app.services.ocr_service as mod
barrier=Barrier(4)
class FakeService:
    def __init__(self):
        barrier.wait(timeout=10)
mod.OCRService=FakeService
mod.get_ocr_service.cache_clear()
with ThreadPoolExecutor(max_workers=4) as pool:
    instances=list(pool.map(lambda _:mod.get_ocr_service(),range(4)))
print('concurrent first calls:',len(instances))
print('distinct service instances:',len({id(s) for s in instances}))
print('sequential subsequent calls share instance:',mod.get_ocr_service() is mod.get_ocr_service())
PY
```

**Salida observada:**

```text
concurrent first calls: 4
distinct service instances: 4
sequential subsequent calls share instance: True
```

**Resultado:** R-04 y R-05 siguen abiertos; los detalles y las decisiones
propuestas están en el plan.

## 2026-11-15 — Correcciones de la revisión OCR

**Estado:** correcciones implementadas a nivel de servicio. La admisión antes de
parsear multipart, los límites globales y el endpoint real siguen pendientes.
Las decisiones y limitaciones se detallan en `docs/OCR_REVIEW_PLAN.md`.

- R-01: estimación conservadora de píxeles antes del renderizado y rechazo de
  páginas excesivas, con umbral configurable; falta calibrarlo en despliegue.
- R-02: trabajo supervisado en subprocessos POSIX, directorio temporal privado,
  límite total y limpieza de grupo de procesos en timeout o cierre controlado.
- R-03: orientación visible primero y segunda pasada ante baja confianza;
  pruebas reales con rotación física, metadatos, recortes y cuadros.
- R-04: límite local de trabajos y cola, timeout de cola y lectura de stream
  con admisión previa; falta controlar el cuerpo multipart en el endpoint real.
- R-05: servicio creado en el ciclo de vida FastAPI, probado con solicitudes
  concurrentes a una ruta de prueba que utiliza la dependencia compartida.
- R-06: nivel validado y aplicado; registros de aplicación en JSON.

### Incidencia durante la corrección de logs

Tras aplicar inicialmente el nivel sin instalar un manejador para la aplicación,
este comando terminó sin imprimir el mensaje de depuración:

```bash
docker compose exec -T app python -c "import logging; from app.core.logging import configure_logging; configure_logging('DEBUG'); logging.getLogger('app.review').debug('DEBUG_SHOULD_BE_VISIBLE')"
```

**Diagnóstico:** el nivel estaba aplicado, pero la aplicación no tenía un
manejador que emitiera sus registros. **Solución:** instalar un manejador JSON
para `app` y mantener los niveles de Uvicorn configurables. Tras reconstruir la
imagen se ejecutó:

```bash
docker compose exec -T app python -c "import logging; from app.core.logging import configure_logging; configure_logging('DEBUG'); logging.getLogger('app.check').debug('logging-ok')"
```

**Salida relevante observada:**

```json
{"timestamp":"2026-11-15T06:27:18.597653+00:00","level":"DEBUG","logger":"app.check","message":"logging-ok"}
```

### Batería final en la imagen reconstruida

```bash
docker compose up --build -d
docker run --rm \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -e SAMPLE_PDFS_DIR=/samples \
  --entrypoint /bin/sh ml-engineer-test-app:latest \
  -c 'uv pip install --target /tmp/test-deps "pytest==9.2.0" && PYTHONPATH=/tmp/test-deps:/app python -m pytest -p no:cacheprovider /tests/unit /tests/integration'
```

**Salida relevante observada:**

```text
collected 67 items
tests/unit/test_lifecycle_and_logging.py ...
tests/unit/test_ocr_async.py ..............
tests/unit/test_ocr_service.py .....................
tests/integration/test_async_http.py .
tests/integration/test_ocr_real.py .......................
tests/integration/test_worker_cleanup.py ..
67 passed in 19.18s
```

Las pruebas de limpieza usan un subprocesso simulado que ignora `SIGTERM` y
verifican la parada y ausencia de temporales después de cancelación con cierre
controlado y de timeout. No demuestran limpieza ante caída abrupta de la máquina
o `SIGKILL` del contenedor. La prueba HTTP usa una ruta simulada, no `/api/extract`.
`docker compose ps` mostró ambos contenedores activos y `curl --fail --silent
--show-error http://localhost:8000/health` devolvió `{"status":"ok"}`.

## 2026-11-15 — Revisión visual preliminar de cuadros OCR

Se procesó `sample_pdfs/meeting_minutes.pdf` con `OCRService` y se dibujó cada
cuadro de palabra sobre una copia del PDF. Se usó un directorio temporal como
destino inicial y después se copió el archivo, sin sobrescribir, a
`docs_dev/meeting_minutes_ocr_visual.pdf`, que permanece fuera de Git.

```bash
docker run --rm -i --user "$(id -u):$(id -g)" \
  -v "$PWD/sample_pdfs:/samples:ro" \
  -v "<directorio-temporal>:/output" \
  --entrypoint python ml-engineer-test-app:latest - <<'PY'
from pathlib import Path
import pymupdf
from app.services.ocr_service import OCRService

source = Path('/samples/meeting_minutes.pdf')
destination = Path('/output/meeting_minutes_ocr_visual.pdf')
if destination.exists():
    raise SystemExit(f'El archivo ya existe; no se sobrescribió: {destination}')
result = OCRService().extract_file(source)
with pymupdf.open(source) as document:
    for page_result in result.pages:
        page = document[page_result.page_number]
        for word in page_result.words:
            box = word.box
            page.draw_rect(pymupdf.Rect(box.x, box.y, box.x + box.width, box.y + box.height), color=(1, 0, 0), width=0.7, overlay=True)
    document.save(destination, deflate=True)
print(f'PDF generado: {destination}')
print(f'Páginas: {len(result.pages)}; palabras por página: {[len(page.words) for page in result.pages]}')
PY
```

**Salida observada:** `Páginas: 2; palabras por página: [149, 134]`. El usuario
revisó y aprobó visualmente los cuadros para este PDF. Sus observaciones y las
limitaciones de esa revisión están en [MANUAL_TEST_LOG.md](MANUAL_TEST_LOG.md).

## Plantilla para próximas ejecuciones

````markdown
## AAAA-MM-DD — Hito o cambio
**Entorno:** sistema, arquitectura y versiones relevantes.
**Objetivo:** qué se intenta comprobar.
**Comando:**
```bash
comando ejecutado
```
**Salida observada:**
```text
fragmento relevante, sin secretos
```
**Resultado:** correcto / incorrecto / parcial.
**Diagnóstico y solución:** si hubo errores, cómo se investigaron y corrigieron;
si siguen pendientes, indicarlo explícitamente.
**Limitaciones / pendientes:** qué no se probó.
````
