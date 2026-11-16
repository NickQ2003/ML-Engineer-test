"""Check the actual Uvicorn endpoint with real OCR and NER, without mocks."""

import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.request
from uuid import uuid4

import pymupdf

from app.core.config import Settings


BASE = "http://127.0.0.1:8000"
SAMPLE = Path("/samples/meeting_minutes.pdf")
CLIENT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  [OK] {message}", flush=True)


def wait_for_server(seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with CLIENT.open(BASE + "/health", timeout=2) as response:
                if response.status == 200 and json.load(response).get("status") == "ok":
                    return
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            pass
        time.sleep(0.2)
    raise AssertionError("La API temporal no respondió en el plazo de inicio")


def post(pdf: bytes, names: str) -> tuple[int, dict]:
    boundary = ("smoke-" + uuid4().hex).encode("ascii")
    body = (
        b"--" + boundary + b'\r\nContent-Disposition: form-data; name="pdf_file"; filename="documento.pdf"'
        b"\r\nContent-Type: application/pdf\r\n\r\n" + pdf
        + b"\r\n--" + boundary + b'\r\nContent-Disposition: form-data; name="names"\r\n\r\n'
        + names.encode("utf-8") + b"\r\n--" + boundary + b"--\r\n"
    )
    request = urllib.request.Request(
        BASE + "/api/extract", data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary.decode("ascii")},
        method="POST",
    )
    try:
        with CLIENT.open(request, timeout=180) as response:
            status, data = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, data = error.code, error.read()
    try:
        result = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise AssertionError(f"La API devolvió una respuesta no JSON con HTTP {status}") from None
    if not isinstance(result, dict):
        raise AssertionError("La respuesta JSON no es un objeto")
    return status, result


def synthetic_rotated_pdf() -> tuple[bytes, tuple[float, float, float, float]]:
    """Create an image-only scan with a known position, crop and rotation."""
    with pymupdf.open() as original, pymupdf.open() as scanned:
        page = original.new_page(width=500, height=300)
        page.insert_text((100, 100), "Alice Smith met Bob Jones in London.", fontsize=22)
        expected = page.search_for("Alice Smith")[0] + (-50, -30, -50, -30)
        pixels = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        target = scanned.new_page(width=500, height=300)
        target.insert_image(target.rect, pixmap=pixels)
        target.set_cropbox(pymupdf.Rect(50, 30, 450, 270))
        target.set_rotation(90)
        return scanned.tobytes(), tuple(expected)


def main() -> None:
    print("Esperando Uvicorn en el contenedor temporal...", flush=True)
    wait_for_server()
    check(True, "GET /health responde HTTP 200 y status=ok")
    check(SAMPLE.is_file(), "El PDF escaneado de ejemplo está disponible")
    pdf = SAMPLE.read_bytes()
    with pymupdf.open(SAMPLE) as source:
        dimensions = [(page.cropbox.width, page.cropbox.height) for page in source]
    check(len(dimensions) == 2, "El PDF de referencia tiene dos páginas")
    names = json.dumps([{"first_name": "Thomas", "last_name": "Muller"}])

    print("\n1. Endpoint real: PDF y nombres válidos", flush=True)
    status, result = post(pdf, names)
    check(status == 200, "POST /api/extract responde HTTP 200")
    extracted, fuzzy = result.get("extracted_names"), result.get("fuzzy_matches")
    check(isinstance(extracted, list) and isinstance(fuzzy, list), "La respuesta contiene las dos listas del contrato")
    pages = {
        item["bounding_box"]["page_number"] for item in extracted if item["name"] == "Thomas Muller"
    }
    check(pages == {0, 1}, "Thomas Muller está localizado en las páginas 0 y 1")
    for item in extracted:
        box = item["bounding_box"]
        page_number = box["page_number"]
        if type(page_number) is not int or not 0 <= page_number < len(dimensions):
            raise AssertionError("La respuesta contiene una página inválida")
        width, height = dimensions[page_number]
        x, y, w, h = (box[field] for field in ("x", "y", "width", "height"))
        if not (all(math.isfinite(value) for value in (x, y, w, h)) and x >= 0 and y >= 0
                and w > 0 and h > 0 and x + w <= width + 1e-4 and y + h <= height + 1e-4):
            raise AssertionError(f"La página {page_number} contiene un cuadro inválido")
    check(True, "Los cuadros tienen valores válidos y caben en sus páginas")
    exact = [
        item for item in fuzzy
        if item["extracted_name"] == "Thomas Muller"
        and item["matched_name"] == "Thomas Muller"
        and item["score"] == 1.0
    ]
    check(len(exact) == 1, "Hay una única coincidencia fuzzy exacta con score 1.0")
    extracted_names = {item["name"] for item in extracted}
    check(
        all(
            item["extracted_name"] in extracted_names
            and item["matched_name"] == "Thomas Muller"
            and math.isfinite(item["score"]) and 0.0 <= item["score"] <= 1.0
            for item in fuzzy
        ),
        "Las coincidencias corresponden a la extracción y tienen scores entre 0 y 1",
    )

    print("\n2. Endpoint real: lista vacía y entradas inválidas", flush=True)
    status, empty = post(pdf, "[]")
    check(status == 200 and empty.get("fuzzy_matches") == [], "Una lista vacía responde 200 sin matches")
    empty_names = empty.get("extracted_names")
    check(
        isinstance(empty_names, list) and any(item["name"] == "Thomas Muller" for item in empty_names),
        "La lista vacía no impide extraer nombres del PDF",
    )
    status, _ = post(pdf, "esto-no-es-json")
    check(status == 422, "JSON inválido responde HTTP 422")
    status, _ = post(b"esto-no-es-un-pdf", "[]")
    check(status == 400, "Contenido que no es PDF responde HTTP 400")
    # The client and Uvicorn run in the same container with the same settings.
    settings = Settings()
    names_limit = settings.max_names_bytes
    if names_limit > 1024 * 1024 or len(pdf) + names_limit + 2048 > settings.max_request_bytes:
        raise AssertionError("Los límites de la imagen no permiten comprobar el campo names de forma aislada")
    status, _ = post(pdf, "x" * (names_limit + 1))
    check(status == 413, "El campo names superior al límite configurado responde HTTP 413")

    print("\n3. Endpoint real: escaneo sintético recortado y rotado", flush=True)
    synthetic, expected = synthetic_rotated_pdf()
    status, rotated = post(synthetic, json.dumps([{"first_name": "Alice", "last_name": "Smith"}]))
    check(status == 200, "El PDF escaneado recortado y rotado responde HTTP 200")
    found = [item for item in rotated.get("extracted_names", []) if item["name"] == "Alice Smith"]
    check(len(found) == 1, "Alice Smith se detecta una vez en el escaneo sintético")
    box = found[0]["bounding_box"]
    center = pymupdf.Point(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    check(box["page_number"] == 0 and center in pymupdf.Rect(expected), "El centro del cuadro cae en la posición PDF conocida")

    print(f"\nRESULTADO: endpoint real verificado; {len(extracted)} apariciones en el PDF de ejemplo.")
    print("Sin mocks y sin modificar el PDF original.")
    print("LÍMITE: no valida todos los nombres ni reemplaza la revisión visual humana.")


if __name__ == "__main__":
    main()
