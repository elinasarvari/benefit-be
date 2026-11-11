"""Renders seeded pay stubs to marked PDF (clean) or PNG (poor quality) files (FR-003-12)."""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from db.seeds.households import SeededDataset, StubDocument
from db.seeds.ids import rng
from packages.platform.synthetic import stamp_image, stamp_pdf


def stub_lines(stub: StubDocument) -> list[str]:
    return [
        f"{stub.employer} (synthetic)",
        f"Employee: {stub.employee_name}",
        f"Pay frequency: {stub.frequency.value}",
        f"Pay date: {stub.pay_date.isoformat()}",
        f"Gross pay: ${stub.gross_pay:,.2f}",
    ]


def render_pdf(stub: StubDocument) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1)
    pdf.setFont("Helvetica-Bold", 14)
    lines = stub_lines(stub)
    pdf.drawString(72, 700, lines[0])
    pdf.setFont("Helvetica", 11)
    for offset, line in enumerate(lines[1:]):
        pdf.drawString(72, 670 - 20 * offset, line)
    pdf.showPage()
    pdf.save()
    return stamp_pdf(buffer.getvalue())


def render_png(stub: StubDocument, draw: random.Random) -> bytes:
    image = Image.new("L", (612, 400), 255)
    pen = ImageDraw.Draw(image)
    for offset, line in enumerate(stub_lines(stub)):
        pen.text((40, 60 + 40 * offset), line, fill=0)
    image = image.rotate(draw.uniform(-6, 6), expand=False, fillcolor=255)
    image = image.filter(ImageFilter.GaussianBlur(draw.uniform(1.2, 2.2)))
    pixels = image.load()
    if pixels is None:
        raise ValueError("image has no pixel access")
    for _ in range(2500):
        x, y = draw.randrange(image.width), draw.randrange(image.height)
        pixels[x, y] = draw.randrange(256)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return stamp_image(buffer.getvalue())


def write_documents(dataset: SeededDataset, out_dir: Path) -> list[Path]:
    """Write every stub; file names derive from document ids, bytes from the seed only."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for stub in dataset.stubs():
        if stub.poor_quality:
            path = out_dir / f"{stub.document_id}.png"
            path.write_bytes(render_png(stub, rng(f"image:{stub.document_id}")))
        else:
            path = out_dir / f"{stub.document_id}.pdf"
            path.write_bytes(render_pdf(stub))
        paths.append(path)
    return paths
