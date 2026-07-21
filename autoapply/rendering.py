"""Renderiza o JSON Resume adaptado em HTML e PDF (ATS-friendly)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def render_resume(resume: dict, out_dir: Path, slug: str) -> tuple[Path, Path | None]:
    """Gera <slug>.html e, se WeasyPrint estiver instalado, <slug>.pdf.
    Retorna (html_path, pdf_path|None)."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("resume.html.j2").render(r=resume)

    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", slug)[:80]
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{slug}.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path: Path | None = None
    try:
        from weasyprint import HTML  # opcional (pip install autoapply[pdf])

        pdf_path = out_dir / f"{slug}.pdf"
        HTML(string=html).write_pdf(str(pdf_path))
    except ImportError:
        log.info("WeasyPrint não instalado — gerando apenas HTML (pip install 'autoapply[pdf]')")
    except Exception:  # noqa: BLE001
        log.exception("Falha ao gerar PDF; seguindo com HTML")
        pdf_path = None
    return html_path, pdf_path
