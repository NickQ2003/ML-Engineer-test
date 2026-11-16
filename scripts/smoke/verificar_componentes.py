"""Check real OCR, NER, bounding boxes and fuzzy matching on the sample PDF."""

import math
from pathlib import Path

from app.core.config import Settings
from app.models.schemas import NamePair
from app.services.bbox_service import PageLocator
from app.services.fuzzy_service import match_names
from app.services.ner_service import SpacyPersonRecognizer
from app.services.ocr_service import OCRService


SAMPLE = Path("/samples/meeting_minutes.pdf")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"  [OK] {message}", flush=True)


def main() -> None:
    check(SAMPLE.is_file(), "El PDF de ejemplo está disponible")
    settings = Settings()

    print("\n1. OCR real: procesar el PDF", flush=True)
    document = OCRService(settings).extract_file(SAMPLE)
    check(len(document.pages) == 2, "Se procesaron dos páginas")
    for number, page in enumerate(document.pages):
        check(page.page_number == number and bool(page.words), f"La página {number} contiene palabras OCR")
        check(
            all(page.text[word.char_start:word.char_end] == word.text for word in page.words),
            f"Los offsets de palabras coinciden con el texto de la página {number}",
        )

    print("\n2. NER real: detectar personas por página", flush=True)
    recognizer = SpacyPersonRecognizer.load(settings.ner_model, settings.ner_max_page_chars)
    spans_by_page = [recognizer.detect_page(page.text) for page in document.pages]
    for page, spans in zip(document.pages, spans_by_page, strict=True):
        check(
            all(page.text[span.char_start:span.char_end] == span.text for span in spans),
            f"Los spans corresponden al texto OCR de la página {page.page_number}",
        )

    print("\n3. Cuadros reales: localizar spans con las palabras del mismo OCR", flush=True)
    located = []
    omitted = 0
    for page, spans in zip(document.pages, spans_by_page, strict=True):
        locator = PageLocator(page)
        for span in spans:
            result = locator.locate(span)
            if result is None:
                omitted += 1
                continue
            box = result.bounding_box
            valid = (
                box.page_number == page.page_number
                and all(math.isfinite(value) for value in (box.x, box.y, box.width, box.height))
                and box.x >= 0 and box.y >= 0 and box.width > 0 and box.height > 0
                and box.x + box.width <= page.width + 1e-4
                and box.y + box.height <= page.height + 1e-4
            )
            if not valid:
                raise AssertionError(f"Cuadro inválido en la página {page.page_number}")
            located.append(result)
    check(bool(located), "Los cuadros localizados son válidos y caben en sus páginas")
    thomas_pages = {
        result.bounding_box.page_number for result in located if result.name == "Thomas Muller"
    }
    check(thomas_pages == {0, 1}, "Thomas Muller está localizado en las páginas 0 y 1")

    print("\n4. Fuzzy real: comparar nombres completos", flush=True)
    queries = [NamePair(first_name="Thomas", last_name="Muller")]
    matches = match_names([result.name for result in located], queries, settings.fuzzy_threshold)
    exact = [
        match for match in matches
        if match.extracted_name == "Thomas Muller"
        and match.matched_name == "Thomas Muller"
        and match.score == 1.0
    ]
    check(len(exact) == 1, "Hay una pareja fuzzy única para Thomas Muller con score 1.0")
    accents = match_names(
        ["María González"], [NamePair(first_name="Maria", last_name="Gonzalez")],
        settings.fuzzy_threshold,
    )
    check(len(accents) == 1 and accents[0].score == 1.0, "La comparación ignora acentos y mayúsculas")
    partial = match_names(["John"], [NamePair(first_name="John", last_name="Smith")], 0.90)
    check(not partial, "Una subcadena no coincide como nombre completo al umbral 0.90")

    print(f"\nRESUMEN: {len(located)} apariciones localizadas; {omitted} spans omitidos por seguridad.")
    print("RESULTADO: componentes reales verificados; sin HTTP ni mocks.")
    print("LÍMITE: no prueba la precisión general ni sustituye la revisión humana.")


if __name__ == "__main__":
    main()
