"""Renderiza o JSON Resume adaptado em HTML e PDF (ATS-friendly)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def sanitizar_slug(slug: str) -> str:
    """Nome de arquivo seguro a partir de empresa + título da vaga.

    Título real traz barra e pipe ("AI/ML Backend Software Engineer | Senior"). Sem
    isto a barra vira separador de diretório: o arquivo some numa subpasta criada
    sozinha e o PDF sai com nome quebrado.
    """
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", slug).strip("-")[:80] or "curriculo"


def docx_para_pdf(docx_path: Path, out_dir: Path) -> Path | None:
    """Converte o .docx em PDF pelo LibreOffice headless.

    É o que preserva a formatação do currículo original: o caminho antigo
    (HTML + WeasyPrint) redesenhava a página do zero e perdia a tipografia do
    arquivo do usuário.
    """
    import subprocess

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir",
             str(out_dir), str(docx_path)],
            capture_output=True, text=True, timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("LibreOffice indisponível para converter %s: %s", docx_path.name, e)
        return None

    pdf = out_dir / f"{docx_path.stem}.pdf"
    if pdf.exists():
        return pdf
    log.error("Conversão falhou (rc=%s): %s", proc.returncode,
              (proc.stderr or proc.stdout)[:300])
    return None


def render_resume(resume: dict, out_dir: Path, slug: str) -> tuple[Path, Path | None]:
    """Gera <slug>.html e, se WeasyPrint estiver instalado, <slug>.pdf.
    Retorna (html_path, pdf_path|None)."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("resume.html.j2").render(r=resume)

    slug = sanitizar_slug(slug)
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
