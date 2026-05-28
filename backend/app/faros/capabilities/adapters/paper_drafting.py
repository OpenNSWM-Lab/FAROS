import os
from typing import Any, Dict

from app.faros.capabilities.base import BaseCapability
from app.faros.models.artifact import ArtifactRecord
from app.faros.models.capability import CapabilityResult
from app.faros.models.execution import ExecutionContext
from app.faros.models.profile import CapabilityBinding
from app.faros.models.provider import ProviderResult, ProviderTask
from app.modules.paper.service import generate_paper
from app.modules.paper.storage import add_log, create_paper, create_paper_zip, get_paper_latex_dir, list_paper_files, update_paper, write_paper_file


class PaperDraftingCapability(BaseCapability):
    capability_id = "paper_drafting"
    description = "Generate a paper artifact using the current paper generation module."
    default_agent_id = "writer"
    default_skill_ids = ["paper-outline", "section-drafting", "latex-assembly"]
    artifact_types = ["paper_record", "latex_project", "latex_zip", "paper_pdf"]

    def build_provider_task(self, context: ExecutionContext, inputs: Dict[str, Any], binding: CapabilityBinding) -> ProviderTask | None:
        if binding.provider_type != 'tool':
            return None
        selected_candidate = inputs.get("selectedCandidate") or {}
        title = inputs.get("title") or selected_candidate.get("title") or inputs.get("seedQuery") or "FAROS Draft"
        return ProviderTask(
            capability_id=self.capability_id,
            provider=binding.provider,
            model=binding.model,
            options={
                'title': title,
                'targetVenue': inputs.get('targetVenue', 'generic'),
            },
        )

    def execute(self, context: ExecutionContext, inputs: Dict[str, Any]) -> CapabilityResult:
        binding = context.get_binding() or context.get_binding(self.capability_id)
        provider_name = binding.provider if binding else inputs.get("providerName", "moonshot")
        model = binding.model if binding and binding.model else inputs.get("model", "moonshot-v1-8k")

        selected_candidate = inputs.get("selectedCandidate") or {}
        title = inputs.get("title") or selected_candidate.get("title") or inputs.get("seedQuery") or "FAROS Draft"
        notes_parts = []
        if selected_candidate:
            notes_parts.append(f"Selected idea candidate: {selected_candidate.get('title', 'N/A')}")
            notes_parts.append(f"Problem: {selected_candidate.get('problem', '')}")
            notes_parts.append(f"Key insight: {selected_candidate.get('keyInsight', '')}")
        if inputs.get("notes"):
            notes_parts.append(inputs["notes"])

        record = create_paper(
            {
                "title": title,
                "paperType": inputs.get("paperType", "algorithm"),
                "targetVenue": inputs.get("targetVenue", "generic"),
                "planLinkId": inputs.get("planLinkId"),
                "projectId": inputs.get("projectId"),
                "experimentIds": inputs.get("experimentIds", []),
                "figureIds": inputs.get("figureIds", []),
                "runIds": inputs.get("runIds", []),
                "providerName": provider_name,
                "model": model,
                "notes": "\n".join(part for part in notes_parts if part),
            }
        )

        paper = generate_paper(record["id"])
        return self._assemble_result(context, record['id'], paper, event_message=f"Paper drafting completed for {record['id']}")

    def consume_provider_result(self, context: ExecutionContext, inputs: Dict[str, Any], provider_result: ProviderResult) -> CapabilityResult:
        payload = provider_result.payload
        selected_candidate = inputs.get("selectedCandidate") or {}
        title = payload.get('title') or inputs.get("title") or selected_candidate.get("title") or inputs.get("seedQuery") or "FAROS Draft"
        record = create_paper(
            {
                "title": title,
                "paperType": inputs.get("paperType", "algorithm"),
                "targetVenue": payload.get('targetVenue') or inputs.get("targetVenue", "generic"),
                "planLinkId": inputs.get("planLinkId"),
                "projectId": inputs.get("projectId"),
                "experimentIds": inputs.get("experimentIds", []),
                "figureIds": inputs.get("figureIds", []),
                "runIds": inputs.get("runIds", []),
                "providerName": provider_result.provider,
                "model": provider_result.model,
                "notes": provider_result.text,
            }
        )
        latex_dir = get_paper_latex_dir(record['id'])
        for rel_path, content in (payload.get('latexFiles') or {}).items():
            write_paper_file(record['id'], rel_path, content)
        pdf_path = os.path.join(latex_dir, 'main.pdf')
        with open(pdf_path, 'wb') as f:
            f.write((payload.get('pdfPlaceholder') or '%PDF-1.4\n% FAROS placeholder\n').encode('latin1'))
        add_log(record['id'], provider_result.text or 'Tool-backed paper assembly completed')
        paper = update_paper(record['id'], {
            'status': payload.get('paperStatus', 'prepared'),
            'outlineJson': payload.get('outlineJson'),
            'pdfAvailable': True,
        }) or create_paper({})
        return self._assemble_result(context, record['id'], paper, event_message=provider_result.text or f"Tool-backed paper drafting completed for {record['id']}")

    def _assemble_result(self, context: ExecutionContext, paper_id: str, paper: Dict[str, Any], event_message: str) -> CapabilityResult:
        latex_dir = get_paper_latex_dir(paper_id)
        zip_path = create_paper_zip(paper_id)
        pdf_path = os.path.join(latex_dir, "main.pdf")
        files = list_paper_files(paper_id)
        artifacts = [
            ArtifactRecord(
                id=f"{context.run_id}:{self.capability_id}:paper",
                type="paper_record",
                uri=f"paper://{paper_id}",
                producer=self.capability_id,
                summary=f"Paper {paper_id} generated for venue {paper.get('targetVenue', 'generic')}",
                metadata={"paperId": paper_id, "status": paper.get("status")},
            ),
            ArtifactRecord(
                id=f"{context.run_id}:{self.capability_id}:latex",
                type="latex_project",
                uri=latex_dir,
                producer=self.capability_id,
                summary=f"LaTeX project with {len(files)} entries",
                metadata={"paperId": paper_id, "fileCount": len(files)},
            ),
        ]
        if zip_path:
            artifacts.append(
                ArtifactRecord(
                    id=f"{context.run_id}:{self.capability_id}:zip",
                    type="latex_zip",
                    uri=zip_path,
                    producer=self.capability_id,
                    summary="Downloadable LaTeX bundle",
                    metadata={"paperId": paper_id},
                )
            )
        if os.path.isfile(pdf_path):
            artifacts.append(
                ArtifactRecord(
                    id=f"{context.run_id}:{self.capability_id}:pdf",
                    type="paper_pdf",
                    uri=pdf_path,
                    producer=self.capability_id,
                    summary="Compiled paper PDF",
                    metadata={"paperId": paper_id},
                )
            )
        return CapabilityResult(
            status="completed" if paper.get("status") in {"completed", "prepared"} else paper.get("status", "failed"),
            outputs={
                "paperId": paper_id,
                "paperTitle": paper.get("title"),
                "paperStatus": paper.get("status"),
                "paperVenue": paper.get("targetVenue"),
                "pdfAvailable": paper.get("pdfAvailable", False),
                "paperFileCount": len(files),
            },
            artifacts=artifacts,
            events=[{"level": "info", "message": event_message}],
        )
