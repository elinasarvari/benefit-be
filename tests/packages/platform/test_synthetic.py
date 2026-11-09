from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from packages.platform.synthetic import (
    BAND_HEIGHT,
    SYNTHETIC_LABEL,
    is_marked,
    stamp_image,
    stamp_pdf,
    stamp_text,
)

BODY_TEXT = "Pay period ending 2026-01-15"


def make_pdf(pages: int = 2) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for _ in range(pages):
        pdf.drawString(72, 400, BODY_TEXT)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def make_image(fmt: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 220, 240)).save(buffer, format=fmt)
    return buffer.getvalue()


def test_FR_000_14_pdf_stamp_is_visible_and_detected() -> None:
    original = make_pdf()
    assert not is_marked(original, "application/pdf")

    stamped = stamp_pdf(original)

    reader = PdfReader(io.BytesIO(stamped))
    assert is_marked(stamped, "application/pdf")
    assert reader.metadata is not None
    assert reader.metadata.subject == "SYNTHETIC"
    assert all(SYNTHETIC_LABEL in page.extract_text() for page in reader.pages)


@pytest.mark.parametrize(("fmt", "media_type"), [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_FR_000_14_image_stamp_is_visible_and_detected(fmt: str, media_type: str) -> None:
    original = make_image(fmt)
    assert not is_marked(original, media_type)

    stamped = stamp_image(original)

    image = Image.open(io.BytesIO(stamped))
    assert image.format == fmt
    assert image.height > 300  # the label bands are added around the picture
    assert is_marked(stamped, media_type)


@pytest.mark.parametrize(("html", "media_type"), [(False, "text/plain"), (True, "text/html")])
def test_FR_000_14_text_stamp_is_visible_and_detected(html: bool, media_type: str) -> None:
    stamped = stamp_text("Dear applicant", html=html)

    assert SYNTHETIC_LABEL in stamped
    assert stamped.endswith("Dear applicant")
    assert is_marked(stamped.encode(), media_type)
    assert not is_marked(b"Dear applicant", media_type)
    if html:
        assert 'role="note"' in stamped


def test_FR_000_14_stamp_does_not_cover_body() -> None:
    pdf = PdfReader(io.BytesIO(stamp_pdf(make_pdf(pages=1))))
    page = pdf.pages[0]
    height = float(page.mediabox.height)
    label_rows: list[float] = []
    body_rows: list[float] = []

    def visitor(text: str, _cm: object, tm: list[float], _font: object, _size: float) -> None:
        if SYNTHETIC_LABEL in text:
            label_rows.append(tm[5])
        elif BODY_TEXT in text:
            body_rows.append(tm[5])

    page.extract_text(visitor_text=visitor)

    assert body_rows, "the original page text must survive stamping"
    assert label_rows
    assert all(y < BAND_HEIGHT or y > height - BAND_HEIGHT for y in label_rows)
    assert all(BAND_HEIGHT < y < height - BAND_HEIGHT for y in body_rows)

    original = Image.new("RGB", (400, 300), (200, 220, 240))
    buffer = io.BytesIO()
    original.save(buffer, format="PNG")
    stamped = Image.open(io.BytesIO(stamp_image(buffer.getvalue()))).convert("RGB")
    band = (stamped.height - 300) // 2
    assert stamped.crop((0, band, 400, band + 300)).tobytes() == original.tobytes()
