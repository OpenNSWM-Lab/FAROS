"""
PDF Renderer — Generates a formatted research PDF from paper content using fpdf2.

This produces a readable, research-grade PDF without requiring a LaTeX compiler.
The LaTeX bundle is always generated separately for camera-ready compilation.
"""

import os
import re
import logging
import subprocess
from typing import Dict, List, Optional, Any

from app.core.user_context import sanitized_subprocess_env

logger = logging.getLogger(__name__)


def compile_latex_project(project_dir: str, main_tex: str = "main.tex", engine: str = "auto", timeout: int = 180) -> str:
    """Compile a LaTeX paper project using latexmk, returning the generated PDF path."""
    pdf_path = os.path.join(project_dir, os.path.splitext(main_tex)[0] + ".pdf")
    engine_flag = "-pdf"
    if engine == "xelatex" or (engine == "auto" and _requires_xelatex(os.path.join(project_dir, main_tex))):
        engine_flag = "-xelatex"
    cmd = [
        "latexmk",
        engine_flag,
        "-interaction=nonstopmode",
        "-halt-on-error",
        main_tex,
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=sanitized_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        tail = "\n".join((exc.stdout or "").splitlines()[-20:] + (exc.stderr or "").splitlines()[-20:])
        raise RuntimeError(f"latexmk timed out after {timeout}s for {main_tex}: {tail[:2000]}") from exc
    if result.returncode != 0 or not os.path.isfile(pdf_path):
        tail = "\n".join((result.stdout or "").splitlines()[-20:] + (result.stderr or "").splitlines()[-20:])
        raise RuntimeError(f"latexmk failed for {main_tex}: {tail[:2000]}")
    logger.info(f"LaTeX project compiled: {pdf_path}")
    return pdf_path


def _requires_xelatex(main_tex_path: str) -> bool:
    try:
        with open(main_tex_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(4096)
    except OSError:
        return False
    return any(marker in content for marker in ("ctexart", "ctexbook", "ctexrep", "xeCJK", "fontspec"))


def render_paper_pdf(
    output_path: str,
    title: str,
    authors: List[str],
    abstract: str,
    sections: List[Dict[str, str]],
    references: List[Dict[str, Any]],
    figures_dir: Optional[str] = None,
    figure_entries: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Render a research paper as a formatted PDF.

    Args:
        output_path: Where to write the PDF
        title: Paper title
        authors: List of author names
        abstract: Abstract text
        sections: List of {title, content} dicts
        references: List of reference dicts with keys: key, authors, title, venue, year
        figures_dir: Directory containing figure images
        figure_entries: List of {filename, ext, caption, label} dicts

    Returns:
        Path to generated PDF
    """
    from fpdf import FPDF

    class PaperPDF(FPDF):
        def header(self):
            if self.page_no() > 1:
                _set_pdf_font(self, "I", 8)
                self.set_text_color(128, 128, 128)
                short_title = title[:60] + "..." if len(title) > 60 else title
                self.cell(0, 5, _pdf_text(self, short_title), align="C")
                self.ln(8)

        def footer(self):
            self.set_y(-15)
            _set_pdf_font(self, "I", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    pdf = PaperPDF(orientation="P", unit="mm", format="A4")
    font_family = _register_unicode_font(pdf)
    pdf._faros_font_family = font_family or "Helvetica"
    pdf._faros_unicode_font = bool(font_family)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # ── Title ──
    _set_pdf_font(pdf, "B", 16)
    pdf.set_text_color(0, 0, 0)
    pdf.multi_cell(0, 8, _pdf_text(pdf, title), align="C")
    pdf.ln(3)

    # ── Authors ──
    _set_pdf_font(pdf, "", 11)
    pdf.set_text_color(60, 60, 60)
    authors_str = ", ".join(authors) if authors else "Anonymous"
    pdf.multi_cell(0, 6, _pdf_text(pdf, authors_str), align="C")
    pdf.ln(6)

    # ── Abstract ──
    _set_pdf_font(pdf, "B", 11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 7, "Abstract", ln=True)
    _set_pdf_font(pdf, "I", 9.5)
    pdf.set_text_color(30, 30, 30)
    clean_abstract = _strip_latex(abstract, preserve_unicode=bool(getattr(pdf, "_faros_unicode_font", False)))
    pdf.multi_cell(0, 5, clean_abstract)
    pdf.ln(4)
    pdf.set_draw_color(180, 180, 180)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

    # ── Sections ──
    fig_idx = 0
    for sec in sections:
        sec_title = sec.get("title", "Section")
        sec_content = sec.get("content", "")

        # Section heading
        _set_pdf_font(pdf, "B", 12)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, 7, _pdf_text(pdf, sec_title))
        pdf.ln(2)

        # Section body — strip LaTeX commands for readable text
        clean = _strip_latex(sec_content, preserve_unicode=bool(getattr(pdf, "_faros_unicode_font", False)))
        _set_pdf_font(pdf, "", 9.5)
        pdf.set_text_color(30, 30, 30)

        # Split into paragraphs
        paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]
        for para in paragraphs:
            if para.startswith("Algorithm") or para.startswith("ALGORITHM"):
                # Render algorithm block
                _render_algorithm_block(pdf, para)
            elif _is_table_block(para):
                _render_table_block(pdf, para)
            elif _is_equation_line(para):
                _render_equation(pdf, para)
            else:
                pdf.multi_cell(0, 5, para)
                pdf.ln(2)

        # Insert a figure after experiments-like sections
        if figure_entries and figures_dir and fig_idx < len(figure_entries):
            sec_lower = sec_title.lower()
            if any(kw in sec_lower for kw in ["experiment", "result", "analysis", "ablation", "method"]):
                _insert_figure(pdf, figures_dir, figure_entries[fig_idx])
                fig_idx += 1

    # ── Insert remaining figures ──
    if figure_entries and figures_dir:
        while fig_idx < len(figure_entries):
            _insert_figure(pdf, figures_dir, figure_entries[fig_idx])
            fig_idx += 1

    # ── References ──
    pdf.add_page()
    _set_pdf_font(pdf, "B", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 7, "References", ln=True)
    pdf.ln(2)
    _set_pdf_font(pdf, "", 8)
    pdf.set_text_color(40, 40, 40)
    for i, ref in enumerate(references, 1):
        ref_text = f"[{i}] {ref.get('authors', 'Unknown')}. " \
                   f"\"{ref.get('title', 'Untitled')}.\" " \
                   f"{ref.get('venue', 'Preprint')}, {ref.get('year', 2024)}."
        pdf.multi_cell(0, 4, _pdf_text(pdf, ref_text))
        pdf.ln(1)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pdf.output(output_path)
    logger.info(f"PDF rendered: {output_path} ({pdf.page_no()} pages)")
    return output_path


def _register_unicode_font(pdf) -> Optional[str]:
    candidates = [
        os.environ.get("FAROS_PDF_FONT"),
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            pdf.add_font("FarosUnicode", "", path)
            return "FarosUnicode"
        except Exception as exc:
            logger.debug("Failed to load PDF unicode font %s: %s", path, exc)
    return None


def _set_pdf_font(pdf, style: str, size: float) -> None:
    family = getattr(pdf, "_faros_font_family", "Helvetica")
    if getattr(pdf, "_faros_unicode_font", False):
        pdf.set_font(family, "", size)
    else:
        pdf.set_font(family, style, size)


def _pdf_text(pdf, text: str) -> str:
    if getattr(pdf, "_faros_unicode_font", False):
        return text or ""
    return _sanitize_unicode(text or "")


def _sanitize_unicode(text: str) -> str:
    """Replace unicode characters that latin-1 can't encode with ASCII equivalents."""
    replacements = {
        '\u2014': '--', '\u2013': '-', '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"', '\u2026': '...', '\u2022': '*',
        '\u00a0': ' ', '\u2003': ' ', '\u2002': ' ', '\u200b': '',
        '\u2212': '-', '\u00d7': 'x', '\u2264': '<=', '\u2265': '>=',
        '\u2260': '!=', '\u221e': 'inf', '\u2208': 'in', '\u2209': 'not in',
        '\u2211': 'sum', '\u220f': 'prod', '\u222b': 'int',
        '\u03b1': 'alpha', '\u03b2': 'beta', '\u03b3': 'gamma', '\u03b4': 'delta',
        '\u03b5': 'epsilon', '\u03b8': 'theta', '\u03bb': 'lambda', '\u03bc': 'mu',
        '\u03c0': 'pi', '\u03c3': 'sigma', '\u03c4': 'tau', '\u03c6': 'phi',
        '\u2192': '->', '\u2190': '<-', '\u21d2': '=>', '\u2248': '~=',
        '\u2261': '===', '\u2282': 'subset', '\u2283': 'superset',
        '\u222a': 'union', '\u2229': 'intersect', '\u2205': 'empty',
    }
    for u, a in replacements.items():
        text = text.replace(u, a)
    # Final pass: replace any remaining non-latin1 chars
    return text.encode('latin-1', errors='replace').decode('latin-1')


def _strip_latex(text: str, preserve_unicode: bool = False) -> str:
    """Strip LaTeX markup to produce readable plain text."""
    t = text
    # Remove common LaTeX commands
    t = re.sub(r'\\(section|subsection|subsubsection|paragraph)\*?\{([^}]*)\}', r'\2', t)
    t = re.sub(r'\\(textbf|textit|emph|underline)\{([^}]*)\}', r'\2', t)
    t = re.sub(r'\\(cite|ref|label|eqref)\{[^}]*\}', '', t)
    t = re.sub(r'\\(begin|end)\{[^}]*\}', '', t)
    t = re.sub(r'\\(usepackage|documentclass|bibliography|bibliographystyle)\{[^}]*\}', '', t)
    t = re.sub(r'\\(maketitle|newpage|clearpage|tableofcontents)', '', t)
    t = re.sub(r'\\[a-zA-Z]+\*?(\[[^\]]*\])?\{([^}]*)\}', r'\2', t)
    t = re.sub(r'\\[a-zA-Z]+', '', t)
    t = re.sub(r'[{}$\\]', '', t)
    t = re.sub(r'&', ' | ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = t.strip()
    return t if preserve_unicode else _sanitize_unicode(t)


def _is_equation_line(text: str) -> bool:
    """Check if text looks like a standalone equation."""
    stripped = text.strip()
    return (stripped.startswith("$$") or
            "\\begin{equation}" in stripped or
            (len(stripped) < 200 and any(c in stripped for c in "=∑∏∫")))


def _is_table_block(text: str) -> bool:
    """Check if text looks like a table."""
    return ("\\begin{table" in text or
            text.count("|") > 4 or
            "\\hline" in text or
            "\\toprule" in text)


def _render_equation(pdf, text: str):
    """Render an equation-like block."""
    _set_pdf_font(pdf, "I", 9)
    pdf.set_text_color(0, 0, 128)
    clean = _strip_latex(text, preserve_unicode=bool(getattr(pdf, "_faros_unicode_font", False))).strip()
    if clean:
        pdf.multi_cell(0, 5, f"  {clean}")
    pdf.ln(3)
    _set_pdf_font(pdf, "", 9.5)
    pdf.set_text_color(30, 30, 30)


def _render_algorithm_block(pdf, text: str):
    """Render an algorithm pseudo-code block."""
    pdf.set_fill_color(245, 245, 250)
    pdf.set_draw_color(100, 100, 180)
    x, y = pdf.get_x(), pdf.get_y()
    w = pdf.w - pdf.l_margin - pdf.r_margin

    _set_pdf_font(pdf, "B", 9)
    pdf.set_text_color(0, 0, 128)
    pdf.cell(w, 6, "Algorithm", border="TB", ln=True, fill=True)
    _set_pdf_font(pdf, "", 8)
    pdf.set_text_color(30, 30, 30)

    lines = text.split("\n")
    for line in lines[:30]:
        clean = _strip_latex(line, preserve_unicode=bool(getattr(pdf, "_faros_unicode_font", False))).strip()
        if clean:
            pdf.cell(w, 4, f"  {clean[:100]}", ln=True, fill=True)
    pdf.ln(3)
    _set_pdf_font(pdf, "", 9.5)
    pdf.set_text_color(30, 30, 30)


def _render_table_block(pdf, text: str):
    """Render a table block."""
    _set_pdf_font(pdf, "", 8)
    pdf.set_text_color(30, 30, 30)
    lines = text.split("\n")
    for line in lines[:20]:
        clean = _strip_latex(line, preserve_unicode=bool(getattr(pdf, "_faros_unicode_font", False))).strip()
        if clean and clean not in ("\\toprule", "\\midrule", "\\bottomrule", "\\hline"):
            pdf.cell(0, 4, clean[:120], ln=True)
    pdf.ln(3)
    _set_pdf_font(pdf, "", 9.5)


def _insert_figure(pdf, figures_dir: str, fig_entry: Dict[str, str]):
    """Insert a figure image into the PDF."""
    fname = fig_entry.get("filename", "")
    caption = fig_entry.get("caption", "")
    # Prefer PNG for PDF embedding
    for ext in ["png", "pdf", "jpg"]:
        fpath = os.path.join(figures_dir, f"{fname}.{ext}")
        if os.path.isfile(fpath) and ext != "pdf":  # fpdf2 can't embed PDF figures
            try:
                pdf.ln(4)
                avail_w = pdf.w - pdf.l_margin - pdf.r_margin
                pdf.image(fpath, x=pdf.l_margin + 10, w=avail_w - 20)
                pdf.ln(2)
                _set_pdf_font(pdf, "I", 8)
                pdf.set_text_color(80, 80, 80)
                pdf.multi_cell(0, 4, _pdf_text(pdf, f"Figure: {caption}"))
                pdf.ln(4)
                _set_pdf_font(pdf, "", 9.5)
                pdf.set_text_color(30, 30, 30)
                return
            except Exception as e:
                logger.warning(f"Failed to insert figure {fname}: {e}")
    # If no image could be inserted, add caption text only
    if caption:
        pdf.ln(2)
        _set_pdf_font(pdf, "I", 8)
        pdf.set_text_color(80, 80, 80)
        pdf.multi_cell(0, 4, _pdf_text(pdf, f"[Figure: {caption}]"))
        pdf.ln(2)
        _set_pdf_font(pdf, "", 9.5)
        pdf.set_text_color(30, 30, 30)
