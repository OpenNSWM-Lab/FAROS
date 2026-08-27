import os
import re
from typing import Any, Dict, List, Tuple

from app.modules.paper.storage import update_paper
from .base import PaperSkillContext, PaperSkillResult
from .utils import (
    LATEX_MATH_ENVS,
    normalize_duplicate_latex_labels,
    sanitize_latex_text_specials,
)


UNICODE_LATEX_REPLACEMENTS: Dict[str, Tuple[str, str]] = {
    "≥": (r"\geq", r"$\geq$"),
    "≤": (r"\leq", r"$\leq$"),
    "≈": (r"\approx", r"$\approx$"),
    "±": (r"\pm", r"$\pm$"),
    "×": (r"\times", r"$\times$"),
    "→": (r"\to", r"$\to$"),
    "←": (r"\leftarrow", r"$\leftarrow$"),
    "↔": (r"\leftrightarrow", r"$\leftrightarrow$"),
    "α": (r"\alpha", r"$\alpha$"),
    "β": (r"\beta", r"$\beta$"),
    "γ": (r"\gamma", r"$\gamma$"),
    "δ": (r"\delta", r"$\delta$"),
    "ε": (r"\epsilon", r"$\epsilon$"),
    "θ": (r"\theta", r"$\theta$"),
    "λ": (r"\lambda", r"$\lambda$"),
    "μ": (r"\mu", r"$\mu$"),
    "π": (r"\pi", r"$\pi$"),
    "σ": (r"\sigma", r"$\sigma$"),
    "τ": (r"\tau", r"$\tau$"),
    "Δ": (r"\Delta", r"$\Delta$"),
    "Σ": (r"\Sigma", r"$\Sigma$"),
}
TEXT_REPLACEMENTS = {
    "−": "-",
    "–": "--",
    "—": "---",
}
UNICODE_SUBSCRIPTS = {
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₅": "5",
    "₆": "6",
    "₇": "7",
    "₈": "8",
    "₉": "9",
}
UNICODE_SUPERSCRIPTS = {
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
}
REQUIRED_PACKAGE_RULES: Tuple[Tuple[str, str, str], ...] = (
    (r"\\multirow\b", r"\usepackage{multirow}", "multirow"),
    (r"\\makecell\b", r"\usepackage{makecell}", "makecell"),
    (r"\\begin\{adjustbox\}", r"\usepackage{adjustbox}", "adjustbox"),
    (r"\\begin\{landscape\}", r"\usepackage{pdflscape}", "pdflscape"),
    (r"\\ding\b", r"\usepackage{pifont}", "pifont"),
    (r"\\(rowcolor|cellcolor|columncolor)\b", r"\usepackage[table]{xcolor}", "xcolor"),
)


def _iter_tex_files(latex_dir: str) -> List[str]:
    tex_files: List[str] = []
    for root, _dirs, files in os.walk(latex_dir):
        if "artifacts" in os.path.relpath(root, latex_dir).split(os.sep):
            continue
        for filename in files:
            if filename.endswith(".tex"):
                tex_files.append(os.path.join(root, filename))
    return sorted(tex_files)


def _replace_unicode_latex_chars(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    rewrites: List[Dict[str, Any]] = []
    out: List[str] = []
    math_env_stack: List[str] = []
    inline_math_stack: List[str] = []
    i = 0
    while i < len(content):
        ch = content[i]

        if content.startswith(r"\[", i) or content.startswith(r"\(", i):
            delimiter = content[i : i + 2]
            inline_math_stack.append(delimiter)
            out.append(delimiter)
            i += 2
            continue

        if content.startswith(r"\]", i) or content.startswith(r"\)", i):
            delimiter = content[i : i + 2]
            opener = r"\[" if delimiter == r"\]" else r"\("
            if opener in inline_math_stack:
                for idx in range(len(inline_math_stack) - 1, -1, -1):
                    if inline_math_stack[idx] == opener:
                        del inline_math_stack[idx:]
                        break
            out.append(delimiter)
            i += 2
            continue

        if ch == "\\" and i + 1 < len(content):
            match = re.match(r"\\(begin|end)\{([^}]+)\}", content[i:])
            if match:
                command, env_name = match.group(1), match.group(2)
                out.append(match.group(0))
                if command == "begin" and env_name in LATEX_MATH_ENVS:
                    math_env_stack.append(env_name)
                elif command == "end" and env_name in math_env_stack:
                    for idx in range(len(math_env_stack) - 1, -1, -1):
                        if math_env_stack[idx] == env_name:
                            del math_env_stack[idx:]
                            break
                i += len(match.group(0))
                continue
            out.append(ch)
            out.append(content[i + 1])
            i += 2
            continue

        if content.startswith("$$", i):
            delimiter = "$$"
            if inline_math_stack and inline_math_stack[-1] == delimiter:
                inline_math_stack.pop()
            else:
                inline_math_stack.append(delimiter)
            out.append(delimiter)
            i += 2
            continue

        if ch == "$":
            delimiter = "$"
            if inline_math_stack and inline_math_stack[-1] == delimiter:
                inline_math_stack.pop()
            else:
                inline_math_stack.append(delimiter)
            out.append(ch)
            i += 1
            continue

        in_math = bool(inline_math_stack or math_env_stack)
        if ch in UNICODE_LATEX_REPLACEMENTS:
            math_repl, text_repl = UNICODE_LATEX_REPLACEMENTS[ch]
            replacement = math_repl if in_math else text_repl
            out.append(replacement)
            rewrites.append({"kind": "unicode_math", "from": ch, "to": replacement})
        elif ch in TEXT_REPLACEMENTS:
            replacement = TEXT_REPLACEMENTS[ch]
            out.append(replacement)
            rewrites.append({"kind": "unicode_text", "from": ch, "to": replacement})
        elif ch in UNICODE_SUBSCRIPTS:
            replacement = f"_{{{UNICODE_SUBSCRIPTS[ch]}}}" if in_math else f"$_{{{UNICODE_SUBSCRIPTS[ch]}}}$"
            out.append(replacement)
            rewrites.append({"kind": "unicode_subscript", "from": ch, "to": replacement})
        elif ch in UNICODE_SUPERSCRIPTS:
            replacement = f"^{{{UNICODE_SUPERSCRIPTS[ch]}}}" if in_math else f"$^{{{UNICODE_SUPERSCRIPTS[ch]}}}$"
            out.append(replacement)
            rewrites.append({"kind": "unicode_superscript", "from": ch, "to": replacement})
        else:
            out.append(ch)
        i += 1
    return "".join(out), rewrites


def _fix_algorithm_text_math_lines(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    rewrites: List[Dict[str, Any]] = []

    def replace_line(match: re.Match[str]) -> str:
        indent, text_label, expr = match.group(1), match.group(2), match.group(3).strip()
        expr = expr.rstrip("$").strip().replace(r"\_", "_")
        replacement = f"{indent}$\\text{{{text_label}}} {expr}$\\;"
        rewrites.append({"kind": "algorithm_text_math", "from": match.group(0), "to": replacement})
        return replacement

    normalized = re.sub(
        r"^(\s*)\\text\{([^}]*)\}\s+(.+?)\$\\;",
        replace_line,
        content,
        flags=re.MULTILINE,
    )
    return normalized, rewrites


def _fix_algorithm_float_spec(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    normalized = content.replace(r"\begin{algorithm}[H]", r"\begin{algorithm}[t]")
    if normalized == content:
        return content, []
    count = content.count(r"\begin{algorithm}[H]")
    return normalized, [
        {
            "kind": "algorithm_float_spec",
            "from": r"\begin{algorithm}[H]",
            "to": r"\begin{algorithm}[t]",
        }
        for _ in range(count)
    ]


def _fix_math_operator_subscripts(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    rewrites: List[Dict[str, Any]] = []
    replacements = {
        r"\argmax\_": r"\operatorname*{arg\,max}_",
        r"\argmin\_": r"\operatorname*{arg\,min}_",
    }
    normalized = content
    for source, target in replacements.items():
        count = normalized.count(source)
        if not count:
            continue
        normalized = normalized.replace(source, target)
        rewrites.extend(
            {"kind": "math_operator_subscript", "from": source, "to": target}
            for _ in range(count)
        )
    return normalized, rewrites


def _restore_corrupted_superscript_stars(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Undo Markdown emphasis accidentally created from two math `^*` tokens."""

    marker = r"^\emph{"
    normalized = content
    rewrites: List[Dict[str, Any]] = []
    search_from = 0
    while True:
        start = normalized.find(marker, search_from)
        if start < 0:
            break
        opening_brace = start + len(marker) - 1
        depth = 1
        cursor = opening_brace + 1
        while cursor < len(normalized) and depth:
            char = normalized[cursor]
            backslashes = 0
            check = cursor - 1
            while check >= 0 and normalized[check] == "\\":
                backslashes += 1
                check -= 1
            escaped = backslashes % 2 == 1
            if not escaped and char == "{":
                depth += 1
            elif not escaped and char == "}":
                depth -= 1
            cursor += 1
        if depth:
            search_from = opening_brace + 1
            continue
        original = normalized[start:cursor]
        inner = normalized[opening_brace + 1 : cursor - 1]
        replacement = "^*" + inner + "*"
        normalized = normalized[:start] + replacement + normalized[cursor:]
        rewrites.append({
            "kind": "restore_superscript_star",
            "from": original,
            "to": replacement,
        })
        search_from = start + len(replacement)
    return normalized, rewrites


def _redirect_unique_missing_refs(
    sections_content: Dict[str, str],
) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """Redirect a dangling ref only when one suffixed label is unambiguous."""

    label_re = re.compile(r"\\label\{([^}]+)\}")
    ref_re = re.compile(r"\\(eqref|ref|autoref|pageref|cref|Cref)\{([^}]+)\}")
    labels = {
        label
        for content in sections_content.values()
        for label in label_re.findall(content)
    }
    rewrites: List[Dict[str, str]] = []
    normalized: Dict[str, str] = {}
    for section_id, content in sections_content.items():
        def replace_ref(match: re.Match[str]) -> str:
            command, raw = match.group(1), match.group(2)
            parts = [part.strip() for part in raw.split(",")]
            redirected: List[str] = []
            changed = False
            for value in parts:
                if value in labels:
                    redirected.append(value)
                    continue
                candidates = sorted(label for label in labels if label.startswith(value + ":"))
                if len(candidates) == 1:
                    redirected.append(candidates[0])
                    changed = True
                    rewrites.append({
                        "section": section_id,
                        "from": value,
                        "to": candidates[0],
                    })
                else:
                    redirected.append(value)
            if not changed:
                return match.group(0)
            return f"\\{command}{{{', '.join(redirected)}}}"

        normalized[section_id] = ref_re.sub(replace_ref, content)
    return normalized, rewrites


def _remove_duplicate_abstract_input(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    if "\\begin{abstract}" not in content:
        return content, []
    normalized = re.sub(
        r"\n\s*\\input\{sections/abstract\.tex\}\s*\n",
        "\n",
        content,
    )
    if normalized == content:
        return content, []
    return normalized, [{"kind": "duplicate_abstract_input", "from": r"\input{sections/abstract.tex}", "to": ""}]


def _main_has_package(main_tex: str, package_name: str) -> bool:
    return re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b" + re.escape(package_name) + r"\b[^}]*\}", main_tex) is not None


def _insert_package(main_tex: str, package_line: str) -> str:
    lines = main_tex.splitlines()
    last_package_index = -1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(r"\usepackage") or stripped.startswith(r"\RequirePackage"):
            last_package_index = index
    if last_package_index >= 0:
        lines.insert(last_package_index + 1, package_line)
        return "\n".join(lines) + ("\n" if main_tex.endswith("\n") else "")
    begin_index = next((index for index, line in enumerate(lines) if r"\begin{document}" in line), len(lines))
    lines.insert(begin_index, package_line)
    return "\n".join(lines) + ("\n" if main_tex.endswith("\n") else "")


def _ensure_required_packages(
    main_tex: str,
    combined_tex: str,
) -> Tuple[str, List[Dict[str, Any]]]:
    rewrites: List[Dict[str, Any]] = []
    normalized = main_tex
    for pattern, package_line, package_name in REQUIRED_PACKAGE_RULES:
        if not re.search(pattern, combined_tex):
            continue
        if _main_has_package(normalized, package_name):
            continue
        normalized = _insert_package(normalized, package_line)
        rewrites.append({"kind": "missing_package", "from": package_name, "to": package_line})
    return normalized, rewrites


def _ensure_xcolor_table_option(main_tex: str, combined_tex: str) -> Tuple[str, List[Dict[str, Any]]]:
    if not re.search(r"\\(rowcolor|cellcolor|columncolor)\b", combined_tex):
        return main_tex, []

    match = re.search(r"\\usepackage(?:\[([^\]]*)\])?\{([^}]*\bxcolor\b[^}]*)\}", main_tex)
    if not match:
        return main_tex, []

    options = [item.strip() for item in (match.group(1) or "").split(",") if item.strip()]
    if "table" in options:
        return main_tex, []

    options.append("table")
    replacement = f"\\usepackage[{','.join(options)}]{{{match.group(2)}}}"
    normalized = main_tex[: match.start()] + replacement + main_tex[match.end():]
    return normalized, [{"kind": "package_option", "from": match.group(0), "to": replacement}]


def preflight_latex_project(latex_dir: str) -> List[Dict[str, Any]]:
    """Apply deterministic LaTeX fixes before latexmk compilation."""
    rewrites: List[Dict[str, Any]] = []
    tex_files = _iter_tex_files(latex_dir)
    file_contents: Dict[str, str] = {}
    for path in tex_files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                file_contents[path] = handle.read()
        except OSError:
            continue
    original_contents = dict(file_contents)

    section_paths = {
        os.path.splitext(os.path.basename(path))[0]: path
        for path in tex_files
        if os.path.relpath(path, latex_dir).split(os.sep)[0] == "sections"
    }
    normalized_sections, duplicate_label_rewrites = normalize_duplicate_latex_labels({
        section_id: file_contents[path]
        for section_id, path in section_paths.items()
        if path in file_contents
    })
    for section_id, content in normalized_sections.items():
        file_contents[section_paths[section_id]] = content
    rewrites.extend(
        {"file": f"sections/{rewrite['section']}.tex", "kind": "duplicate_label", **rewrite}
        for rewrite in duplicate_label_rewrites
    )
    redirected_sections, redirected_ref_rewrites = _redirect_unique_missing_refs(
        normalized_sections
    )
    for section_id, content in redirected_sections.items():
        file_contents[section_paths[section_id]] = content
    rewrites.extend(
        {"file": f"sections/{rewrite['section']}.tex", "kind": "dangling_ref", **rewrite}
        for rewrite in redirected_ref_rewrites
    )

    main_path = os.path.join(latex_dir, "main.tex")
    if main_path in file_contents:
        combined_tex = "\n".join(file_contents.values())
        main_tex, xcolor_rewrites = _ensure_xcolor_table_option(file_contents[main_path], combined_tex)
        if xcolor_rewrites:
            file_contents[main_path] = main_tex
            for rewrite in xcolor_rewrites:
                rewrites.append({"file": "main.tex", **rewrite})
        main_tex, package_rewrites = _ensure_required_packages(file_contents[main_path], combined_tex)
        if package_rewrites:
            file_contents[main_path] = main_tex
            for rewrite in package_rewrites:
                rewrites.append({"file": "main.tex", **rewrite})

    for path, content in file_contents.items():
        original = original_contents.get(path, content)
        file_rewrites: List[Dict[str, Any]] = []
        content, step_rewrites = _restore_corrupted_superscript_stars(content)
        file_rewrites.extend(step_rewrites)
        # main.tex is trusted template code. Escaping its comments and macro
        # parameters turns `%` and `##1` into invalid preamble text.
        if os.path.basename(path) != "main.tex":
            sanitized = sanitize_latex_text_specials(content)
            if sanitized != content:
                file_rewrites.append({"kind": "text_specials"})
                content = sanitized
        content, step_rewrites = _replace_unicode_latex_chars(content)
        file_rewrites.extend(step_rewrites)
        content, step_rewrites = _fix_algorithm_text_math_lines(content)
        file_rewrites.extend(step_rewrites)
        content, step_rewrites = _fix_algorithm_float_spec(content)
        file_rewrites.extend(step_rewrites)
        content, step_rewrites = _fix_math_operator_subscripts(content)
        file_rewrites.extend(step_rewrites)
        if os.path.basename(path) == "main.tex":
            content, step_rewrites = _remove_duplicate_abstract_input(content)
            file_rewrites.extend(step_rewrites)

        if content != original:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            rel_path = os.path.relpath(path, latex_dir)
            for rewrite in file_rewrites:
                rewrites.append({"file": rel_path, **rewrite})
    return rewrites


def latex_log_quality_errors(compile_log: str) -> List[str]:
    errors = []
    for pattern, message in (
        (r"LaTeX Warning: (?:Reference|Citation) `[^']+'[^\n]*undefined", "undefined references or citations remain"),
        (r"LaTeX Warning: Label `[^']+' multiply defined", "duplicate labels remain"),
        (r"There were undefined references", "undefined references remain"),
    ):
        if re.search(pattern, compile_log):
            errors.append(message)
    return list(dict.fromkeys(errors))


def compile_latex_once(ctx: PaperSkillContext) -> PaperSkillResult:
    pdf_path = os.path.join(ctx.latex_dir, "main.pdf")
    status = "unknown"
    size = 0
    errors = None
    preflight_rewrites = preflight_latex_project(ctx.latex_dir)

    try:
        from app.services.pdf_renderer import compile_latex_project
        compile_latex_project(ctx.latex_dir)
        log_path = os.path.join(ctx.latex_dir, "main.log")
        if os.path.isfile(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                compile_log = handle.read()
            unresolved = latex_log_quality_errors(compile_log)
            if unresolved:
                raise RuntimeError(
                    "LaTeX quality gate failed: "
                    + "; ".join(dict.fromkeys(unresolved))
                )
        if os.path.isfile(pdf_path):
            size = os.path.getsize(pdf_path)
        update_paper(ctx.paper_id, {
            "pdfAvailable": True,
            "compileStatus": "latexmk",
            "pdfRenderMode": "latexmk",
            "compileErrors": None,
        })
        status = "latexmk"
    except Exception as exc:
        errors = str(exc)[:2000]
        update_paper(ctx.paper_id, {
            "pdfAvailable": False,
            "compileStatus": "failed",
            "pdfRenderMode": None,
            "compileErrors": errors,
        })
        status = "failed"

    return PaperSkillResult(
        name="latex_compile_once",
        summary=f"{status} ({size} bytes)" if size else status,
        data={
            "pdf_available": status != "failed",
            "compile_status": status,
            "compile_errors": errors,
            "compile_preflight_rewrites": preflight_rewrites,
            "compile_output_size": size,
        },
    )
