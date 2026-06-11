"""Platform-owned templates API implementation."""

import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.modules.paper.storage import add_log, get_paper, get_paper_latex_dir, update_paper, write_paper_file

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["templates"])

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_TEMPLATES_DIR = _BACKEND_ROOT / "templates" / "latex"
_PROMPT_GUIDE_FILENAMES = {"style_guide.md", "writing_guide.md", "prompt_guide.md"}


def _load_registry() -> list:
    path = _TEMPLATES_DIR / "templates.json"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class TemplateInfo(BaseModel):
    id: str
    name: str
    description: str
    sections: List[str]
    bibStyle: str


class ApplyTemplateRequest(BaseModel):
    paperId: str
    templateId: str
    title: Optional[str] = None
    authors: Optional[str] = None


class ApplyTemplateResponse(BaseModel):
    ok: bool
    paperId: str
    templateId: str
    filesWritten: int
    message: str = ""


@router.get("", summary="List available LaTeX templates")
async def list_templates() -> dict:
    registry = _load_registry()
    return {"templates": registry, "total": len(registry)}


@router.get("/{template_id}", summary="Get template details")
async def get_template(template_id: str) -> dict:
    registry = _load_registry()
    template = next((item for item in registry if item["id"] == template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")

    template_dir = os.path.join(_TEMPLATES_DIR, template_id)
    files = []
    if os.path.isdir(template_dir):
        for filename in os.listdir(template_dir):
            file_path = os.path.join(template_dir, filename)
            if os.path.isfile(file_path):
                files.append({"name": filename, "size": os.path.getsize(file_path)})
    return {**template, "files": files}


@router.post("/apply", summary="Apply a template to a paper")
async def apply_template(req: ApplyTemplateRequest) -> ApplyTemplateResponse:
    paper = get_paper(req.paperId)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper '{req.paperId}' not found")

    registry = _load_registry()
    template = next((item for item in registry if item["id"] == req.templateId), None)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{req.templateId}' not found")

    template_dir = os.path.join(_TEMPLATES_DIR, req.templateId)
    if not os.path.isdir(template_dir):
        raise HTTPException(status_code=404, detail=f"Template directory not found for '{req.templateId}'")

    get_paper_latex_dir(req.paperId)
    title = req.title or paper.get("title", "Untitled Paper")
    authors = req.authors or "Author Name"
    section_placeholders = {
        "Introduction": "%%INTRODUCTION%%",
        "Related Work": "%%RELATED_WORK%%",
        "Background": "%%BACKGROUND%%",
        "Preliminaries": "%%PRELIMINARIES%%",
        "Method": "%%METHOD%%",
        "Proposed Method": "%%METHOD%%",
        "Methodology": "%%METHOD%%",
        "Experiments": "%%EXPERIMENTS%%",
        "Experimental Evaluation": "%%EXPERIMENTS%%",
        "Setup": "%%SETUP%%",
        "Experimental Setup": "%%SETUP%%",
        "Results": "%%RESULTS%%",
        "Results and Discussion": "%%RESULTS%%",
        "Ablations": "%%ABLATIONS%%",
        "Analysis": "%%ANALYSIS%%",
        "Discussion": "%%DISCUSSION%%",
        "Task Definition": "%%TASK_DEFINITION%%",
        "Error Analysis": "%%ERROR_ANALYSIS%%",
        "Conclusion": "%%CONCLUSION%%",
        "待研究问题": "%%PROBLEM_STATEMENT%%",
        "解决思路": "%%RATIONALE%%",
        "必要的技术手段": "%%TECHNICAL_DETAILS%%",
        "数据集": "%%DATASETS%%",
        "Source": "%%SOURCE%%",
        "Target": "%%TARGET%%",
        "标题": "%%PAPER_TITLE%%",
        "摘要": "%%PAPER_ABSTRACT%%",
        "方法论": "%%METHODS%%",
        "实验设计": "%%EXPERIMENTS%%",
        "实验结果": "%%RESULTS%%",
        "参考论文": "%%REFERENCES%%",
    }
    section_inputs = []
    for section_name in template.get("sections", []):
        placeholder = section_placeholders.get(section_name)
        if placeholder:
            section_inputs.append(f"\\section{{{section_name}}}\n{placeholder}")

    placeholders = {
        "%%TITLE%%": title,
        "%%RUNNING_TITLE%%": title[:70],
        "%%AUTHORS%%": authors,
        "%%ABSTRACT%%": "TODO: Write abstract here.",
        "%%SECTION_INPUTS%%": "\n\n".join(section_inputs),
        "%%INTRODUCTION%%": "TODO: Write introduction.",
        "%%RELATED_WORK%%": "TODO: Write related work.",
        "%%BACKGROUND%%": "TODO: Write background.",
        "%%PRELIMINARIES%%": "TODO: Write preliminaries.",
        "%%METHOD%%": "TODO: Write method.",
        "%%EXPERIMENTS%%": "TODO: Write experiments.",
        "%%SETUP%%": "TODO: Write experimental setup.",
        "%%RESULTS%%": "TODO: Write results.",
        "%%ABLATIONS%%": "TODO: Write ablation studies.",
        "%%ANALYSIS%%": "TODO: Write analysis.",
        "%%DISCUSSION%%": "TODO: Write discussion.",
        "%%ERROR_ANALYSIS%%": "TODO: Write error analysis.",
        "%%TASK_DEFINITION%%": "TODO: Write task definition.",
        "%%CONCLUSION%%": "TODO: Write conclusion.",
        "%%PROBLEM_STATEMENT%%": "TODO: 明确指出当前领域存在的具体局限性。",
        "%%RATIONALE%%": "TODO: 基于逻辑推理阐述创新点，并展示从证据到假设的推导链条。",
        "%%TECHNICAL_DETAILS%%": "TODO: 列出验证假设所需的统计、机器学习、深度学习或实验技术栈。",
        "%%DATASETS%%": "TODO: 填写来源合规、真实可核查的数据集及其许可、规模和变量说明。",
        "%%SOURCE%%": "TODO: 描述假设推演依据的历史数据、公开统计、既有实验结果或真实文献证据。",
        "%%TARGET%%": "TODO: 描述验证实验拟采集的数据特征、样本范围、变量、采集频率、伦理与隐私约束。",
        "%%PAPER_TITLE%%": title,
        "%%PAPER_ABSTRACT%%": "TODO: 用中文撰写包含背景、方法、预期结果和意义的完整摘要。",
        "%%METHODS%%": "TODO: 写出具体实施步骤，包括模型架构、变量定义、实验流程和质量控制。",
        "%%REFERENCES%%": "TODO: 列出真实可核查参考论文，并在 refs.bib 中维护 BibTeX 条目。",
    }

    files_written = 0
    for filename in os.listdir(template_dir):
        if filename in _PROMPT_GUIDE_FILENAMES:
            continue
        source = os.path.join(template_dir, filename)
        if not os.path.isfile(source):
            continue
        with open(source, "r", encoding="utf-8") as handle:
            content = handle.read()
        if filename.endswith(".tex"):
            for key, value in placeholders.items():
                content = content.replace(key, value)
        write_paper_file(req.paperId, filename, content)
        files_written += 1

    update_paper(req.paperId, {"templateId": req.templateId})
    add_log(req.paperId, f"Applied template '{req.templateId}' ({template['name']}), {files_written} files written")

    return ApplyTemplateResponse(
        ok=True,
        paperId=req.paperId,
        templateId=req.templateId,
        filesWritten=files_written,
        message=f"Template '{template['name']}' applied with {files_written} files",
    )
