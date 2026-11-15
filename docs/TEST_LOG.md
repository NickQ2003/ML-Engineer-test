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
La publicación de la rama se verifica por separado; crear el fork no demuestra
por sí solo que el push haya funcionado.

### Comprobaciones pendientes

- Escribir datos en Qdrant, reiniciar el contenedor y comprobar su persistencia.
- Probar la imagen en Linux x86_64; la ejecución anterior fue en Linux ARM64.
- Comprobar el adaptador Qdrant, modelos reales y endpoints de extracción y RAG
  cuando se implementen; no forman parte de las pruebas ya ejecutadas.

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
