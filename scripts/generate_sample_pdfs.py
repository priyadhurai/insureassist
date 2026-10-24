"""Turn the synthetic Markdown sources in samples/sources/ into PDFs in samples/pdfs/.

Usage:  uv run python scripts/generate_sample_pdfs.py              # all sources
        uv run python scripts/generate_sample_pdfs.py auto-policy-amendment   # just one

Regenerating a PDF changes its bytes (PyMuPDF stamps a creation time), so its
document ID changes too. Regenerate only what you edited.

Supports a small Markdown subset: # / ## / ### headings, paragraphs, "- " bullets,
"|" tables and "<!-- pagebreak -->" (start a new page). Each page gets a footer,
like real policy documents, so ingestion also sees repeated header/footer text.
"""

import html
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "samples" / "sources"
OUTPUT = ROOT / "samples" / "pdfs"
PAGE_BREAK = "<!-- pagebreak -->"

CSS = """
* { font-family: sans-serif; }
h1 { font-size: 18px; margin-bottom: 8px; }
h2 { font-size: 14px; margin-top: 12px; }
h3 { font-size: 12px; margin-top: 8px; }
p, li, td, th { font-size: 10px; line-height: 1.4; }
table { border-collapse: collapse; }
td, th { border: 1px solid #888; padding: 3px 6px; }
"""


def markdown_to_html(markdown: str) -> str:
    """Convert the supported Markdown subset to HTML (one page's worth)."""
    parts: list[str] = []
    bullets: list[str] = []
    rows: list[list[str]] = []

    def flush() -> None:
        # Close any open bullet list or table before starting another element.
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()
        if rows:
            header, *body = rows
            head = "".join(f"<th>{c}</th>" for c in header)
            cells = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in body)
            parts.append(f"<table><tr>{head}</tr>{cells}</table>")
            rows.clear()

    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("|"):
            cells = [html.escape(c.strip()) for c in line.strip("|").split("|")]
            if not all(set(c) <= {"-", ":"} for c in cells):  # skip the |---| divider row
                rows.append(cells)
            continue
        if line.startswith("- "):
            bullets.append(html.escape(line[2:]))
            continue
        flush()
        if not line:
            continue
        level = len(line) - len(line.lstrip("#"))
        if 1 <= level <= 3 and line[level : level + 1] == " ":
            parts.append(f"<h{level}>{html.escape(line[level + 1 :])}</h{level}>")
        else:
            parts.append(f"<p>{html.escape(line)}</p>")
    flush()
    return "".join(parts)


def build_pdf(source: Path, target: Path) -> int:
    text = source.read_text(encoding="utf-8")
    title = text.splitlines()[0].lstrip("# ").strip()
    page_rect = pymupdf.paper_rect("letter")
    content_area = page_rect + (60, 60, -60, -70)  # margins; leave room for the footer

    writer = pymupdf.DocumentWriter(str(target))
    for page_markdown in text.split(PAGE_BREAK):
        story = pymupdf.Story(html=markdown_to_html(page_markdown), user_css=CSS)
        more = True
        while more:  # a long section simply continues on the next page
            device = writer.begin_page(page_rect)
            more, _ = story.place(content_area)
            story.draw(device)
            writer.end_page()
    writer.close()

    # Second pass: add a footer to every page.
    doc = pymupdf.open(target)
    for page in doc:
        footer = (
            f"Example Mutual Insurance Co. - {title} - SAMPLE (fictional) - "
            f"Page {page.number + 1} of {doc.page_count}"
        )
        page.insert_text((60, page_rect.height - 40), footer, fontsize=8, color=(0.4, 0.4, 0.4))
    page_count = doc.page_count
    doc.saveIncr()
    doc.close()
    return page_count


def main(names: list[str]) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sources = [SOURCES / f"{n}.md" for n in names] if names else sorted(SOURCES.glob("*.md"))
    for source in sources:
        target = OUTPUT / f"{source.stem}.pdf"
        pages = build_pdf(source, target)
        print(f"{target.relative_to(ROOT)}  ({pages} pages)")


if __name__ == "__main__":
    main(sys.argv[1:])
