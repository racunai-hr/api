from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from expenses.validators import detect_invoice_kind

MAX_PDF_PAGES = 4


class RenderError(Exception):
    """PDF/image could not be prepared for vision."""


@dataclass(frozen=True)
class RenderedPage:
    mime: str
    content: bytes


def _png_mime() -> str:
    return 'image/png'


def render_invoice_pages(content: bytes) -> list[RenderedPage]:
    kind = detect_invoice_kind(content[:8])
    if kind == 'jpeg':
        return [RenderedPage(mime='image/jpeg', content=content)]
    if kind == 'png':
        return [RenderedPage(mime='image/png', content=content)]
    if kind != 'pdf':
        raise RenderError('Nepodržan format datoteke.')
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise RenderError('PDF podrška nije instalirana.') from exc
    try:
        document = pdfium.PdfDocument(content)
    except Exception as exc:
        raise RenderError('PDF se nije mogao otvoriti.') from exc
    pages: list[RenderedPage] = []
    try:
        count = min(len(document), MAX_PDF_PAGES)
        if count < 1:
            raise RenderError('PDF nema stranica.')
        for index in range(count):
            page = document[index]
            bitmap = page.render(scale=2.0)
            pil = bitmap.to_pil()
            buffer = io.BytesIO()
            pil.save(buffer, format='PNG')
            pages.append(RenderedPage(mime=_png_mime(), content=buffer.getvalue()))
    finally:
        document.close()
    return pages


def page_data_url(page: RenderedPage) -> str:
    encoded = base64.b64encode(page.content).decode('ascii')
    return f'data:{page.mime};base64,{encoded}'
