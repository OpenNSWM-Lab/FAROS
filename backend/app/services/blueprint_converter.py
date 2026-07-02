"""Convert PlanPackage to the frontend ExperimentBlueprint DAG format."""

from __future__ import annotations

from typing import Any, Dict, List

from app.models.plan_package import PlanPackage, PlanStage, PlanStep


def convert_plan_package_to_blueprint(pp: PlanPackage) -> Dict[str, Any]:
    """Convert a PlanPackage into the ExperimentBlueprint dict consumed by the frontend DAG."""

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    edge_index: set[str] = set()

    stage_order: Dict[str, int] = {s.id: s.order for s in pp.stages}

    def _add_edge(source: str, target: str) -> None:
        eid = f"{source}__{target}"
        if eid not in edge_index:
            edge_index.add(eid)
            edges.append({"id": f"e-{len(edges)}", "source": source, "target": target})

    # --- Stage header nodes ---
    for stage in pp.stages:
        nodes.append({
            "id": f"stage-{stage.id}",
            "label": f"Stage {stage.order}: {stage.title}",
            "stage": stage.id,
            "status": "pending",
            "description": stage.goal,
            "method": stage.method,
            "inputs": list(stage.dependsOn) if stage.dependsOn else [],
            "outputs": [],
            "result": None,
            "startedAt": None,
            "finishedAt": None,
            "duration": None,
            "type": "stage-header",
        })

    # --- Step nodes ---
    for stage in pp.stages:
        for step in stage.steps:
            nodes.append({
                "id": step.id,
                "label": f"{step.order}. {step.title}",
                "stage": stage.id,
                "status": "pending",
                "description": step.desc,
                "method": step.method,
                "inputs": list(step.inputFrom) if step.inputFrom else [],
                "outputs": [o.name for o in step.outputs],
                "result": {
                    "expected": [
                        {"metric": e.metric, "target": e.target, "desc": e.desc}
                        for e in step.expected
                    ],
                } if step.expected else None,
                "startedAt": None,
                "finishedAt": None,
                "duration": None,
            })

    if not pp.stages:
        return {
            "id": pp.packageId,
            "title": pp.researchQuestion,
            "description": pp.background.summary,
            "hypothesis": pp.hypothesis,
            "constants": pp.constants,
            "nodes": nodes,
            "edges": edges,
        }

    # --- Edges ---

    # 1. Stage-level dependencies (dependsOn)
    for stage in pp.stages:
        for dep_id in stage.dependsOn:
            _add_edge(f"stage-{dep_id}", f"stage-{stage.id}")

    # 2. Stage header → first steps in that stage
    for stage in pp.stages:
        step_ids_in_stage = {s.id for s in stage.steps}
        for step in stage.steps:
            has_internal_input = any(
                inp in step_ids_in_stage for inp in (step.inputFrom or [])
            )
            if not has_internal_input:
                _add_edge(f"stage-{stage.id}", step.id)

    # 3. Step-level dependencies (inputFrom)
    for stage in pp.stages:
        for step in stage.steps:
            for inp_id in (step.inputFrom or []):
                _add_edge(inp_id, step.id)

    # 4. Last steps → next stage header (by order or dependsOn)
    for stage in pp.stages:
        step_ids_in_stage = {s.id for s in stage.steps}
        referenced: set[str] = set()
        for s in stage.steps:
            for inp_id in (s.inputFrom or []):
                if inp_id in step_ids_in_stage:
                    referenced.add(inp_id)
        last_steps = [s for s in stage.steps if s.id not in referenced]

        # Find stages that list this stage in dependsOn
        next_stage_ids = [
            s.id for s in pp.stages if stage.id in (s.dependsOn or [])
        ]
        if not next_stage_ids:
            # Fallback: next by numeric order
            next_stage_ids = [
                s.id for s in pp.stages if s.order == stage.order + 1
            ]

        for next_id in next_stage_ids:
            for step in last_steps:
                _add_edge(step.id, f"stage-{next_id}")

    return {
        "id": pp.packageId,
        "title": pp.researchQuestion,
        "description": pp.background.summary,
        "hypothesis": pp.hypothesis,
        "constants": pp.constants,
        "nodes": nodes,
        "edges": edges,
    }
