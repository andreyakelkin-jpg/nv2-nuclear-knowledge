"""Text extraction and conservative, page-aware extraction quality signals."""
from __future__ import annotations

from pathlib import Path
import re


PAGE_MARKER = re.compile(r"^===== СТРАНИЦА (\d+) =====$", re.MULTILINE)
SPARSE_PAGE_CHARS = 80
VERY_SPARSE_PDF_CHARS = 25


def _page_text(page_number: int, text: str) -> str:
    return f"===== СТРАНИЦА {page_number} =====\n{text.strip()}"


def _pdf_is_very_sparse(pages: list[str]) -> bool:
    """Detect the common image-PDF outcome without assuming OCR is available."""
    if not pages:
        return True
    nonempty = [page.strip() for page in pages if page.strip()]
    return not nonempty or sum(len(page) for page in nonempty) < max(
        VERY_SPARSE_PDF_CHARS, len(pages) * VERY_SPARSE_PDF_CHARS
    )


def _extract_with_pypdf(source: Path) -> list[str]:
    from pypdf import PdfReader

    return [(page.extract_text() or "") for page in PdfReader(str(source)).pages]


def _extract_with_pdfplumber(source: Path) -> list[str]:
    import pdfplumber

    with pdfplumber.open(source) as document:
        return [(page.extract_text() or "") for page in document.pages]


def _extract_docx(source: Path) -> str:
    """Emit paragraphs and tables in the order that Word stores body blocks."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(source)
    parts: list[str] = []
    table_number = 0
    for child in document.element.body.iterchildren():
        # Avoid private type coupling: the namespace tag is stable in OOXML.
        if child.tag.endswith("}p"):
            text = Paragraph(child, document).text.strip()
            if text:
                parts.append(text)
        elif child.tag.endswith("}tbl"):
            table_number += 1
            table = Table(child, document)
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
            parts.append(f"[ТАБЛИЦА {table_number}]")
            parts.extend(row for row in rows if row.strip(" |"))
    return "\n".join(parts)


def extract_text(source: Path) -> tuple[str, str]:
    """Extract supported source text without OCR.

    PDF text carries explicit source-page boundaries.  pdfplumber is only a
    text-extraction fallback when pypdf fails or yields an implausibly scant
    result; it is not an OCR fallback.
    """
    suffix = source.suffix.lower()
    if suffix in {".txt", ".md"}:
        return source.read_text(encoding="utf-8", errors="replace"), "plain-text"
    if suffix == ".docx":
        return _extract_docx(source), "python-docx"
    if suffix != ".pdf":
        raise ValueError("Допустимы только PDF, DOCX, TXT и MD")

    method = "pypdf"
    try:
        pages = _extract_with_pypdf(source)
        if _pdf_is_very_sparse(pages):
            pages = _extract_with_pdfplumber(source)
            method = "pdfplumber"
    except Exception:
        try:
            pages = _extract_with_pdfplumber(source)
            method = "pdfplumber"
        except Exception as fallback_error:
            raise RuntimeError(f"Не удалось извлечь текст PDF: {source.name}") from fallback_error
    return "\n\n".join(_page_text(number, page) for number, page in enumerate(pages, 1)), method


def _marked_pages(text: str) -> list[tuple[int, str]]:
    markers = list(PAGE_MARKER.finditer(text))
    pages: list[tuple[int, str]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        pages.append((int(marker.group(1)), text[marker.end():end].strip()))
    return pages


def extraction_quality(text: str, method: str) -> dict[str, object]:
    """Return compact quality indicators, checking each marked PDF page."""
    pages = _marked_pages(text)
    sparse = [number for number, page_text in pages if len(page_text) < SPARSE_PAGE_CHARS]
    warnings: list[str] = []
    if not text.strip():
        warnings.append("empty_text")
    if method in {"pypdf", "pdfplumber"} and not pages:
        warnings.append("pdf_page_markers_missing")
    if sparse:
        warnings.append("empty_or_sparse_pages:" + ",".join(str(number) for number in sparse))
    return {
        "pages_detected": len(pages),
        "empty_or_sparse_pages": sparse,
        "requires_visual_review": bool(warnings),
        "warnings": warnings,
    }
