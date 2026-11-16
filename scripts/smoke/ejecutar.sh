#!/bin/bash
# Run real checks in disposable containers; never stop existing Compose services.
set -euo pipefail

SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SMOKE_DIR/../.." && pwd)"
IMAGE="${SMOKE_IMAGE:-document-intelligence-smoke:local}"
CONTAINER=""
CLIENT_PID=""

usage() {
  echo "Uso: bash scripts/smoke/ejecutar.sh componentes|http|todo"
  echo "Imagen predeterminada: document-intelligence-smoke:local (alternativa: SMOKE_IMAGE)."
}

if [ "$#" -ne 1 ]; then
  usage >&2
  exit 2
fi
case "$1" in
  componentes|http|todo) MODE="$1" ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker no está disponible. Consulta el README del proyecto." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "No se puede conectar con Docker. Comprueba que el motor esté iniciado." >&2
  exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "No se encontró la imagen local: $IMAGE" >&2
  echo "Desde la raíz, construye la imagen: docker build -t document-intelligence-smoke:local ." >&2
  echo "Si usas otra imagen, indica su nombre mediante SMOKE_IMAGE." >&2
  exit 1
fi
if [ ! -f "$ROOT/sample_pdfs/meeting_minutes.pdf" ]; then
  echo "Falta sample_pdfs/meeting_minutes.pdf en el repositorio." >&2
  exit 1
fi

cleanup() {
  local result=$?
  local stopped=0
  trap - EXIT
  if [ -n "$CONTAINER" ]; then
    echo "Cerrando únicamente el contenedor HTTP temporal: ${CONTAINER:0:12}"
    if docker stop "$CONTAINER" >/dev/null; then
      stopped=1
    else
      echo "No se pudo detener el contenedor temporal $CONTAINER; comprueba su estado." >&2
      if [ "$result" -eq 0 ]; then
        result=1
      fi
    fi
  fi
  if [ "$stopped" -eq 1 ] && [ -n "$CLIENT_PID" ]; then
    wait "$CLIENT_PID" 2>/dev/null || true
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

components() {
  echo "=== Componentes reales: OCR → NER → cuadros → fuzzy ==="
  echo "Contenedor temporal sin red; código, scripts y PDF montados en solo lectura."
  docker run --rm --network none \
    -v "$ROOT/app:/app/app:ro" \
    -v "$ROOT/sample_pdfs:/samples:ro" \
    -v "$SMOKE_DIR:/smoke:ro" \
    -e PYTHONPATH=/app --entrypoint python "$IMAGE" -u /smoke/verificar_componentes.py
}

http() {
  echo "=== HTTP real: Uvicorn → endpoint → OCR → NER → cuadros → fuzzy ==="
  echo "Iniciando una API temporal sin red externa ni puertos publicados en el host..."
  CONTAINER="$(docker run --rm -d --network none \
    -v "$ROOT/app:/app/app:ro" \
    -v "$ROOT/sample_pdfs:/samples:ro" \
    -v "$SMOKE_DIR:/smoke:ro" \
    -e PYTHONPATH=/app --entrypoint uvicorn "$IMAGE" \
    app.main:app --host 127.0.0.1 --port 8000)"
  echo "Contenedor temporal: ${CONTAINER:0:12}"
  # Waiting on a background client lets Bash handle INT/TERM before it finishes.
  docker exec "$CONTAINER" python -u /smoke/verificar_http.py &
  CLIENT_PID=$!
  if ! wait "$CLIENT_PID"; then
    CLIENT_PID=""
    echo "La verificación HTTP falló. Últimos mensajes del servidor temporal:" >&2
    docker logs --tail 30 "$CONTAINER" >&2 || true
    return 1
  fi
  CLIENT_PID=""
}

echo "Imagen local: $IMAGE"
case "$MODE" in
  componentes) components ;;
  http) http ;;
  todo) components; http ;;
esac
