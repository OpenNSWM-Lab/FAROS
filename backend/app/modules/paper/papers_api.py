"""
Papers API — Research-grade LaTeX paper generation with quality gates.

Endpoints:
- GET/POST /papers
- GET /papers/{id}
- POST /papers/{id}/generate
- GET /papers/{id}/tree
- GET /papers/{id}/files?path=
- POST /papers/{id}/files  (save edited file content)
- GET /papers/{id}/pdf
- GET /papers/{id}/download/latex.zip
- GET /papers/{id}/download/pdf
- GET /papers/types
- GET /papers/venues
"""

import os
import logging
import threading
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, status, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.settings import get_settings
from app.modules.paper.storage import (
    create_paper, get_paper as _get_paper, list_papers as _list_papers,
    update_paper as _update_paper, list_paper_files as _list_paper_files,
    read_paper_file as _read_paper_file, write_paper_file as _write_paper_file,
    create_paper_zip, get_paper_latex_dir, add_log as _add_log,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/papers", tags=["papers"])

PAPER_TYPES = [
    "algorithm", "application", "survey", "benchmark", "system", "security", "position"
]


VENUES = ["icml", "neurips", "iclr", "acl", "generic", "challenge_cup"]


class CreatePaperRequest(BaseModel):
    title: str = "Untitled Paper"
    paperType: str = "algorithm"
    targetVenue: str = "generic"
    planLinkId: Optional[str] = None
    projectId: Optional[str] = None
    experimentIds: List[str] = Field(default_factory=list)
    figureIds: List[str] = Field(default_factory=list)
    runIds: List[str] = Field(default_factory=list)
    providerName: Optional[str] = None
    model: Optional[str] = None
    notes: Optional[str] = None


class SaveFileRequest(BaseModel):
    path: str
    content: str


class GeneratePaperBriefRequest(BaseModel):
    briefUserEdits: Optional[str] = None
    force: bool = True


class UpdatePaperBriefRequest(BaseModel):
    briefJson: Optional[Dict[str, Any]] = None
    briefUserEdits: Optional[str] = None


class GeneratePaperOutlineRequest(BaseModel):
    force: bool = True


class UpdatePaperOutlineRequest(BaseModel):
    outlineJson: Dict[str, Any]


class UpdatePaperContextRequest(BaseModel):
    planLinkId: Optional[str] = None
    projectId: Optional[str] = None
    experimentIds: List[str] = Field(default_factory=list)
    runIds: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


@router.get("/types")
async def get_paper_types():
    return {"types": PAPER_TYPES}


@router.get("/venues")
async def get_paper_venues():
    return {"venues": VENUES}


@router.get("")
async def list_papers_endpoint():
    papers = _list_papers()
    return {"papers": papers, "total": len(papers)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_paper_endpoint(req: CreatePaperRequest):
    if req.paperType not in PAPER_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid paperType. Must be one of: {PAPER_TYPES}")
    if req.targetVenue not in VENUES:
        raise HTTPException(status_code=400, detail=f"Invalid targetVenue. Must be one of: {VENUES}")
    settings = get_settings()
    provider_name = req.providerName or settings.get_active_provider()
    model = req.model or settings.get_active_model(provider_name)
    record = create_paper(req.model_dump() | {"providerName": provider_name, "model": model})
    return record


@router.get("/{paper_id}")
async def get_paper_endpoint(paper_id: str):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    files = _list_paper_files(paper_id)
    record["fileCount"] = len([f for f in files if not f["isDir"]])
    return record




@router.patch("/{paper_id}/context", status_code=status.HTTP_200_OK)
async def update_paper_context_endpoint(paper_id: str, req: UpdatePaperContextRequest):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    updates = {
        "planLinkId": req.planLinkId,
        "projectId": req.projectId,
        "experimentIds": req.experimentIds,
        "runIds": req.runIds,
        "notes": req.notes,
    }
    return _update_paper(paper_id, updates)


@router.get("/{paper_id}/brief", status_code=status.HTTP_200_OK)
async def get_paper_brief_endpoint(paper_id: str):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    return {
        "paperId": paper_id,
        "brief": record.get("briefJson"),
        "briefUserEdits": record.get("briefUserEdits", ""),
        "briefStatus": record.get("briefStatus", "missing"),
    }


@router.patch("/{paper_id}/brief", status_code=status.HTTP_200_OK)
async def update_paper_brief_endpoint(paper_id: str, req: UpdatePaperBriefRequest):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    updates: Dict[str, Any] = {}
    if req.briefJson is not None:
        updates["briefJson"] = req.briefJson
        updates["briefStatus"] = "user_edited"
    if req.briefUserEdits is not None:
        updates["briefUserEdits"] = req.briefUserEdits
    updated = _update_paper(paper_id, updates) if updates else record
    return {
        "paperId": paper_id,
        "brief": updated.get("briefJson"),
        "briefUserEdits": updated.get("briefUserEdits", ""),
        "briefStatus": updated.get("briefStatus", "missing"),
    }


@router.post("/{paper_id}/brief/generate", status_code=status.HTTP_200_OK)
async def generate_paper_brief_endpoint(paper_id: str, req: GeneratePaperBriefRequest):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    if record.get("status") == "generating":
        raise HTTPException(status_code=409, detail="Paper generation already in progress")
    try:
        from app.modules.paper.service import generate_paper_brief
        updated = generate_paper_brief(
            paper_id,
            brief_user_edits=req.briefUserEdits,
            force=req.force,
        )
    except Exception as exc:
        logger.error(f"Paper brief generation failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc
    return {
        "paperId": paper_id,
        "brief": updated.get("briefJson"),
        "briefUserEdits": updated.get("briefUserEdits", ""),
        "briefStatus": updated.get("briefStatus", "missing"),
    }


@router.get("/{paper_id}/outline", status_code=status.HTTP_200_OK)
async def get_paper_outline_endpoint(paper_id: str):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    return {
        "paperId": paper_id,
        "outline": record.get("outlineJson"),
        "outlineStatus": record.get("outlineStatus", "missing"),
    }


@router.patch("/{paper_id}/outline", status_code=status.HTTP_200_OK)
async def update_paper_outline_endpoint(paper_id: str, req: UpdatePaperOutlineRequest):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    updated = _update_paper(paper_id, {
        "outlineJson": req.outlineJson,
        "outlineStatus": "user_edited",
    })
    return {
        "paperId": paper_id,
        "outline": updated.get("outlineJson") if updated else req.outlineJson,
        "outlineStatus": updated.get("outlineStatus", "user_edited") if updated else "user_edited",
    }


@router.post("/{paper_id}/outline/generate", status_code=status.HTTP_200_OK)
async def generate_paper_outline_endpoint(paper_id: str, req: GeneratePaperOutlineRequest):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    if record.get("status") == "generating":
        raise HTTPException(status_code=409, detail="Paper generation already in progress")
    try:
        from app.modules.paper.service import generate_paper_outline
        updated = generate_paper_outline(paper_id, force=req.force)
    except Exception as exc:
        logger.error(f"Paper outline generation failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc
    return {
        "paperId": paper_id,
        "outline": updated.get("outlineJson"),
        "outlineStatus": updated.get("outlineStatus", "missing"),
    }


@router.post("/{paper_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_paper_endpoint(paper_id: str):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    if record.get("status") == "generating":
        raise HTTPException(status_code=409, detail="Paper generation already in progress")

    def _run():
        try:
            from app.modules.paper.service import generate_paper
            generate_paper(paper_id)
        except Exception as e:
            logger.error(f"Paper generation background task failed: {e}", exc_info=True)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"message": "Paper generation started", "paperId": paper_id}


@router.get("/{paper_id}/tree")
async def get_paper_tree(paper_id: str):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    files = _list_paper_files(paper_id)
    return {"paperId": paper_id, "entries": files}


@router.get("/{paper_id}/files")
async def get_paper_file(paper_id: str, path: str = Query(..., description="Relative file path")):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    content = _read_paper_file(paper_id, path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File '{path}' not found in paper")
    return {"paperId": paper_id, "path": path, "content": content}


@router.post("/{paper_id}/files", status_code=status.HTTP_200_OK)
async def save_paper_file(paper_id: str, req: SaveFileRequest):
    """Save edited file content back to the paper's LaTeX project."""
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    if ".." in req.path or req.path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    _write_paper_file(paper_id, req.path, req.content)
    return {"paperId": paper_id, "path": req.path, "saved": True, "size": len(req.content)}




@router.get("/{paper_id}/pdf")
async def get_paper_pdf(paper_id: str):
    """Stream the generated PDF for preview in browser."""
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    latex_dir = get_paper_latex_dir(paper_id)
    pdf_path = os.path.join(latex_dir, "main.pdf")
    if not os.path.isfile(pdf_path):
        raise HTTPException(status_code=404, detail="PDF not available yet. Generate the paper first.")
    return FileResponse(pdf_path, media_type="application/pdf")


@router.get("/{paper_id}/download/latex.zip")
async def download_latex_zip(paper_id: str):
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    zip_path = create_paper_zip(paper_id)
    if not zip_path or not os.path.isfile(zip_path):
        raise HTTPException(status_code=404, detail="No files to download")
    return FileResponse(zip_path, media_type="application/zip", filename=f"{paper_id}-latex.zip")


@router.get("/{paper_id}/download/pdf")
async def download_paper_pdf(paper_id: str):
    """Download the compiled PDF."""
    return await get_paper_pdf(paper_id)


@router.get("/{paper_id}/download.zip")
async def download_paper_zip_legacy(paper_id: str):
    """Legacy endpoint — redirects to /download/latex.zip."""
    return await download_latex_zip(paper_id)


class AddFigureRequest(BaseModel):
    figureId: str


@router.post("/{paper_id}/figures", status_code=status.HTTP_201_CREATED)
async def add_figure_to_paper_endpoint(paper_id: str, req: AddFigureRequest):
    """Add a figure from experiments to a paper's LaTeX figures folder."""
    from app.modules.paper.storage import copy_figure_to_paper
    result = copy_figure_to_paper(paper_id, req.figureId)
    if not result:
        raise HTTPException(status_code=404, detail=f"Figure '{req.figureId}' not found")
    return result


@router.get("/{paper_id}/figures")
async def list_paper_figures_endpoint(paper_id: str):
    """List all figures associated with a paper."""
    from app.modules.paper.storage import get_paper_figures
    figures = get_paper_figures(paper_id)
    return {"paperId": paper_id, "figures": figures, "total": len(figures)}


@router.get("/figures/{figure_id}/latex-ref")
async def get_latex_figure_reference_endpoint(figure_id: str):
    """Generate LaTeX figure reference code."""
    from app.modules.paper.storage import generate_latex_figure_reference
    latex_code = generate_latex_figure_reference(figure_id)
    if not latex_code:
        raise HTTPException(status_code=404, detail=f"Figure '{figure_id}' not found")
    return {"figureId": figure_id, "latex": latex_code}


@router.post("/{paper_id}/render-pdf", status_code=status.HTTP_202_ACCEPTED)
async def render_paper_pdf_endpoint(paper_id: str):
    """Re-render the PDF from current LaTeX files and figures."""
    record = _get_paper(paper_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Paper '{paper_id}' not found")
    
    def _run():
        try:
            from app.services.pdf_renderer import compile_latex_project, render_paper_pdf
            from app.modules.paper.storage import get_paper, update_paper, get_paper_latex_dir, list_paper_files, read_paper_file
            from app.modules.paper.skills.utils import copy_template_assets, dedupe_figure_entries, get_linked_figure_entries
            import os
            
            paper = get_paper(paper_id)
            if not paper:
                return
            
            latex_dir = get_paper_latex_dir(paper_id)
            pdf_path = os.path.join(latex_dir, "main.pdf")
            figures_dir = os.path.join(latex_dir, "figures")
            venue = paper.get("targetVenue", "generic")
            copy_template_assets(venue, paper_id)
            figure_entries = get_linked_figure_entries(paper, ensure_copied=True)
            
            try:
                compile_latex_project(latex_dir)
                update_paper(paper_id, {"pdfAvailable": True})
                logger.info(f"PDF re-rendered successfully via latexmk: {os.path.getsize(pdf_path)} bytes")
                return
            except Exception as compile_error:
                logger.warning(f"LaTeX compile failed during re-render for {paper_id}: {compile_error}", exc_info=True)
            
            outline = paper.get("outlineJson", {
                "title": paper.get("title", "Untitled Paper"),
                "authors": ["Anonymous"],
                "abstract": "",
                "sections": [],
                "references": []
            })
            
            sections = []
            sections_content = {}
            files = list_paper_files(paper_id)
            for file in files:
                if file.get("path", "").startswith("sections/") and file.get("path", "").endswith(".tex"):
                    section_id = os.path.basename(file["path"])[:-4]
                    content = read_paper_file(paper_id, file["path"]) or ""
                    sections.append({"id": section_id, "title": section_id.capitalize()})
                    sections_content[section_id] = content
            
            if os.path.isdir(figures_dir):
                for fname in sorted(os.listdir(figures_dir)):
                    lower = fname.lower()
                    if lower.endswith((".png", ".jpg", ".jpeg", ".pdf")):
                        base_name = os.path.splitext(fname)[0]
                        ext = os.path.splitext(fname)[1].lstrip(".")
                        figure_entries.append({
                            "filename": base_name,
                            "ext": ext,
                            "caption": f"Figure: {base_name}",
                            "label": f"fig:{base_name}"
                        })
            figure_entries = dedupe_figure_entries(figure_entries)
            
            sections_for_pdf = [
                {"title": s.get("title", s["id"]), "content": sections_content.get(s["id"], "")}
                for s in sections
            ]
            render_paper_pdf(
                output_path=pdf_path,
                title=outline.get("title", paper.get("title", "Untitled")),
                authors=outline.get("authors", ["Anonymous"]),
                abstract=outline.get("abstract", ""),
                sections=sections_for_pdf,
                references=outline.get("references", []),
                figures_dir=figures_dir,
                figure_entries=figure_entries,
            )
            update_paper(paper_id, {"pdfAvailable": True})
            logger.info(f"Fallback PDF re-rendered successfully: {os.path.getsize(pdf_path)} bytes")
        except Exception as e:
            logger.error(f"PDF re-render failed for {paper_id}: {e}", exc_info=True)
    
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"message": "PDF rendering started", "paperId": paper_id}
