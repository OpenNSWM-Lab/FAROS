"""Idea-domain storage facade.

This isolates idea-domain callers from the current shared storage layout.
"""

from app.storage.idea_storage import (
    generate_candidate_id,
    generate_literature_id,
    generate_session_id,
    get_candidate_storage,
    get_literature_storage,
    get_session_storage,
    # Dual-Graph storage
    generate_graph_id,
    generate_handoff_id,
    generate_map_id,
    generate_raw_paper_id,
    get_handoff_storage,
    get_literature_graph_storage,
    get_literature_map_storage,
    get_raw_paper_storage,
    get_structured_paper_storage,
    # Phase 2
    generate_evidence_link_id,
    generate_path_seed_id,
    generate_reasoning_kg_id,
    get_evidence_link_storage,
    get_path_seed_storage,
    get_reasoning_kg_storage,
)
from app.storage.research_plan_storage import get_storage as get_plan_storage

__all__ = [
    "generate_candidate_id",
    "generate_literature_id",
    "generate_session_id",
    "get_candidate_storage",
    "get_literature_storage",
    "get_plan_storage",
    "get_session_storage",
    # Dual-Graph storage
    "generate_graph_id",
    "generate_handoff_id",
    "generate_map_id",
    "generate_raw_paper_id",
    "get_handoff_storage",
    "get_literature_graph_storage",
    "get_literature_map_storage",
    "get_raw_paper_storage",
    "get_structured_paper_storage",
    # Phase 2
    "generate_evidence_link_id",
    "generate_path_seed_id",
    "generate_reasoning_kg_id",
    "get_evidence_link_storage",
    "get_path_seed_storage",
    "get_reasoning_kg_storage",
]
