"""The visible SYNTHETIC marker for generated documents (FR-000-14).

The label always goes in a margin band, never over the document body, so the AI extraction in
002 still reads every value. Generators must leave at least BAND_HEIGHT points of page margin.
"""

from __future__ import annotations

import io
import json

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

SYNTHETIC_LABEL = "SYNTHETIC \u2013 NOT A REAL DOCUMENT"
SYNTHETIC_TAG = "SYNTHETIC"
BAND_HEIGHT = 18  # points on a PDF page
IMAGE_BAND_MIN = 28  # pixels


def _overlay(width: float, height: float) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setFillColor(Color(0.85, 0.1, 0.1))
    pdf.setFont("Helvetica-Bold", 9)
    for y in (BAND_HEIGHT / 2 - 3, height - BAND_HEIGHT / 2 - 3):
        pdf.drawCentredString(width / 2, y, SYNTHETIC_LABEL)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def stamp_pdf(data: bytes) -> bytes:
    writer = PdfWriter(clone_from=io.BytesIO(data))
    for page in writer.pages:
        overlay = PdfReader(
            io.BytesIO(_overlay(float(page.mediabox.width), float(page.mediabox.height)))
        )
        page.merge_page(overlay.pages[0])
    writer.add_metadata({"/Subject": SYNTHETIC_TAG, "/Keywords": SYNTHETIC_TAG})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def stamp_image(data: bytes) -> bytes:
    source = Image.open(io.BytesIO(data))
    fmt = source.format or "PNG"
    picture = source.convert("RGB")
    band = max(IMAGE_BAND_MIN, picture.height // 12)
    canvas_image = Image.new("RGB", (picture.width, picture.height + 2 * band), (255, 255, 255))
    canvas_image.paste(picture, (0, band))
    draw = ImageDraw.Draw(canvas_image)
    font = ImageFont.load_default(size=max(10, band // 2))
    for top in (0, band + picture.height):
        draw.text(
            (picture.width / 2, top + band / 2),
            SYNTHETIC_LABEL,
            fill=(217, 26, 26),
            font=font,
            anchor="mm",
        )
    out = io.BytesIO()
    if fmt == "JPEG":
        canvas_image.save(out, format="JPEG", comment=SYNTHETIC_TAG.encode(), quality=95)
    else:
        info = PngInfo()
        info.add_text("Comment", SYNTHETIC_TAG)
        canvas_image.save(out, format="PNG", pnginfo=info)
    return out.getvalue()


def stamp_text(text: str, *, html: bool = False) -> str:
    if html:
        return f'<div role="note">{SYNTHETIC_LABEL}</div>\n{text}'
    return f"{SYNTHETIC_LABEL}\n\n{text}"


def is_marked(data: bytes, media_type: str) -> bool:
    if media_type == "application/pdf":
        reader = PdfReader(io.BytesIO(data))
        subject = reader.metadata.subject if reader.metadata else None
        return subject == SYNTHETIC_TAG and all(
            SYNTHETIC_LABEL in page.extract_text() for page in reader.pages
        )
    if media_type in ("image/png", "image/jpeg"):
        info = Image.open(io.BytesIO(data)).info
        tag = info.get("comment") or info.get("Comment")
        if isinstance(tag, bytes):
            tag = tag.decode(errors="ignore")
        return tag == SYNTHETIC_TAG
    if media_type in ("text/plain", "text/html"):
        return SYNTHETIC_LABEL in data.decode(errors="ignore")
    if media_type == "application/json":  # 003's history and test sets: a top-level flag
        try:
            document = json.loads(data)
        except ValueError:
            return False
        return isinstance(document, dict) and document.get("synthetic") is True
    return False
