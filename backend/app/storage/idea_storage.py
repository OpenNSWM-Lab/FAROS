"""
Idea Generation Storage

Provides file-based storage for idea sessions, literature items, and candidates.
Follows append-only pattern for scientific integrity.
"""

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from app.models.idea import (
    IdeaSession,
    IdeaSessionStatus,
    LiteratureItem,
    IdeaCandidate,
    # Dual-Graph models
    RawPaper,
    LiteratureGraph,
    StructuredPaper,
    LiteratureMap,
    BFTSHandoff,
    # Phase 2 models
    ReasoningKG,
    GraphEvidenceLink,
    ReasoningPathSeed,
    # Step 5 models
    IdeaNode,
    IdeaSearchTree,
    IdeaSearchEdge,
    IdeaSearchReport,
    LiteratureProbeQuery,
    LiteratureProbeResult,
    GraphPatch,
    # Step 6 models
    RankedIdeaOutput,
)


def generate_session_id() -> str:
    """Generate unique session ID."""
    return f"idea_{uuid.uuid4().hex[:12]}"


def generate_literature_id() -> str:
    """Generate unique literature item ID."""
    return f"lit_{uuid.uuid4().hex[:12]}"


def generate_candidate_id() -> str:
    """Generate unique candidate ID."""
    return f"cand_{uuid.uuid4().hex[:12]}"


def _get_data_dir() -> str:
    """Return the backend-level data directory regardless of current cwd."""
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "data")


def _write_json_atomic(path: Path, data: Dict[str, Any], *, default=None) -> None:
    """Write JSON via a unique temp file, with Windows-friendly replace retries."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=default)
            f.flush()
            os.fsync(f.fileno())

        last_error: Optional[OSError] = None
        for attempt in range(5):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                if attempt == 4:
                    break
                time.sleep(0.05 * (attempt + 1))
        if last_error:
            raise last_error
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


class IdeaSessionStorage:
    """Storage for idea generation sessions."""
    
    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "sessions"
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_session_path(self, session_id: str) -> Path:
        return self.base_path / f"{session_id}.json"
    
    def _serialize_session(self, session: IdeaSession) -> Dict[str, Any]:
        data = session.model_dump()
        # Convert datetime to ISO format
        for key in ['createdAt', 'startedAt', 'endedAt']:
            if data.get(key):
                data[key] = data[key].isoformat() if isinstance(data[key], datetime) else data[key]
        if data.get('trace'):
            if data['trace'].get('startedAt'):
                data['trace']['startedAt'] = data['trace']['startedAt'].isoformat() if isinstance(data['trace']['startedAt'], datetime) else data['trace']['startedAt']
            if data['trace'].get('endedAt'):
                data['trace']['endedAt'] = data['trace']['endedAt'].isoformat() if isinstance(data['trace']['endedAt'], datetime) else data['trace']['endedAt']
            for step in data['trace'].get('steps', []):
                if step.get('startedAt'):
                    step['startedAt'] = step['startedAt'].isoformat() if isinstance(step['startedAt'], datetime) else step['startedAt']
                if step.get('endedAt'):
                    step['endedAt'] = step['endedAt'].isoformat() if isinstance(step['endedAt'], datetime) else step['endedAt']
        return data
    
    def _deserialize_session(self, data: Dict[str, Any]) -> IdeaSession:
        # Convert ISO strings back to datetime
        for key in ['createdAt', 'startedAt', 'endedAt']:
            if data.get(key) and isinstance(data[key], str):
                data[key] = datetime.fromisoformat(data[key])
        if data.get('trace'):
            if data['trace'].get('startedAt') and isinstance(data['trace']['startedAt'], str):
                data['trace']['startedAt'] = datetime.fromisoformat(data['trace']['startedAt'])
            if data['trace'].get('endedAt') and isinstance(data['trace']['endedAt'], str):
                data['trace']['endedAt'] = datetime.fromisoformat(data['trace']['endedAt'])
            for step in data['trace'].get('steps', []):
                if step.get('startedAt') and isinstance(step['startedAt'], str):
                    step['startedAt'] = datetime.fromisoformat(step['startedAt'])
                if step.get('endedAt') and isinstance(step['endedAt'], str):
                    step['endedAt'] = datetime.fromisoformat(step['endedAt'])
        return IdeaSession(**data)
    
    def create(self, session: IdeaSession) -> IdeaSession:
        """Create a new session."""
        path = self._get_session_path(session.id)
        if path.exists():
            raise ValueError(f"Session {session.id} already exists")
        
        data = self._serialize_session(session)
        _write_json_atomic(path, data, default=str)
        
        return session
    
    def get(self, session_id: str) -> Optional[IdeaSession]:
        """Get session by ID."""
        path = self._get_session_path(session_id)
        if not path.exists():
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return self._deserialize_session(data)
    
    def update(self, session: IdeaSession) -> IdeaSession:
        """Update an existing session."""
        path = self._get_session_path(session.id)
        if not path.exists():
            raise ValueError(f"Session {session.id} not found")
        
        data = self._serialize_session(session)
        _write_json_atomic(path, data, default=str)
        
        return session
    
    def list_all(self, status: Optional[IdeaSessionStatus] = None) -> List[IdeaSession]:
        """List all sessions, optionally filtered by status."""
        sessions = []
        for path in self.base_path.glob("idea_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                session = self._deserialize_session(data)
                if status is None or session.status == status:
                    sessions.append(session)
            except Exception:
                continue
        return sorted(sessions, key=lambda s: s.createdAt, reverse=True)


class LiteratureStorage:
    """Storage for literature items."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "literature"
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_item_path(self, item_id: str) -> Path:
        return self.base_path / f"{item_id}.json"
    
    def create(self, item: LiteratureItem) -> LiteratureItem:
        """Create a new literature item."""
        path = self._get_item_path(item.id)
        data = item.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data)
        
        return item
    
    def get(self, item_id: str) -> Optional[LiteratureItem]:
        """Get item by ID."""
        path = self._get_item_path(item_id)
        if not path.exists():
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return LiteratureItem(**data)
    
    def list_by_session(self, session_id: str) -> List[LiteratureItem]:
        """List all items for a session."""
        items = []
        for path in self.base_path.glob("lit_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    items.append(LiteratureItem(**data))
            except Exception:
                continue
        return sorted(items, key=lambda i: i.relevanceScore, reverse=True)


class CandidateStorage:
    """Storage for idea candidates."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "candidates"
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def _get_candidate_path(self, candidate_id: str) -> Path:
        return self.base_path / f"{candidate_id}.json"
    
    def create(self, candidate: IdeaCandidate) -> IdeaCandidate:
        """Create a new candidate."""
        path = self._get_candidate_path(candidate.id)
        data = candidate.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data)
        
        return candidate
    
    def get(self, candidate_id: str) -> Optional[IdeaCandidate]:
        """Get candidate by ID."""
        path = self._get_candidate_path(candidate_id)
        if not path.exists():
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return IdeaCandidate(**data)
    
    def list_by_session(self, session_id: str) -> List[IdeaCandidate]:
        """List all candidates for a session."""
        candidates = []
        for path in self.base_path.glob("cand_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    candidates.append(IdeaCandidate(**data))
            except Exception:
                continue
        # Sort by overall score
        return sorted(candidates, key=lambda c: c.overallScore, reverse=True)


# Global storage instances
_session_storage: Optional[IdeaSessionStorage] = None
_literature_storage: Optional[LiteratureStorage] = None
_candidate_storage: Optional[CandidateStorage] = None


def get_session_storage() -> IdeaSessionStorage:
    global _session_storage
    if _session_storage is None:
        _session_storage = IdeaSessionStorage(_get_data_dir())
    return _session_storage


def get_literature_storage() -> LiteratureStorage:
    global _literature_storage
    if _literature_storage is None:
        _literature_storage = LiteratureStorage(_get_data_dir())
    return _literature_storage


def get_candidate_storage() -> CandidateStorage:
    global _candidate_storage
    if _candidate_storage is None:
        _candidate_storage = CandidateStorage(_get_data_dir())
    return _candidate_storage


# =============================================================================
# Dual-Graph Storage: ID Generators
# =============================================================================


def generate_raw_paper_id() -> str:
    """Generate unique raw paper ID."""
    return f"raw_{uuid.uuid4().hex[:12]}"


def generate_graph_id() -> str:
    """Generate unique literature graph ID."""
    return f"lg_{uuid.uuid4().hex[:12]}"


def generate_map_id() -> str:
    """Generate unique literature map ID."""
    return f"lm_{uuid.uuid4().hex[:12]}"


def generate_handoff_id() -> str:
    """Generate unique handoff ID."""
    return f"bh_{uuid.uuid4().hex[:12]}"


# =============================================================================
# Dual-Graph Storage: RawPaper Storage
# =============================================================================


class RawPaperStorage:
    """Storage for raw papers from literature search."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "raw_papers"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, paper_id: str) -> Path:
        return self.base_path / f"{paper_id}.json"

    def create(self, paper: RawPaper) -> RawPaper:
        """Create a new raw paper."""
        path = self._get_path(paper.id)
        if path.exists():
            raise ValueError(f"RawPaper {paper.id} already exists")

        data = paper.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)

        return paper

    def get(self, paper_id: str) -> Optional[RawPaper]:
        """Get raw paper by ID."""
        path = self._get_path(paper_id)
        if not path.exists():
            return None

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return RawPaper(**data)

    def list_by_session(self, session_id: str) -> List[RawPaper]:
        """List all raw papers for a session."""
        papers = []
        for path in self.base_path.glob("raw_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    papers.append(RawPaper(**data))
            except Exception:
                continue
        return sorted(papers, key=lambda p: p.relevanceScore, reverse=True)


# =============================================================================
# Dual-Graph Storage: LiteratureGraph Storage
# =============================================================================


class LiteratureGraphStorage:
    """Storage for literature graphs."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "graphs"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, graph_id: str) -> Path:
        return self.base_path / f"{graph_id}.json"

    def create(self, graph: LiteratureGraph) -> LiteratureGraph:
        """Create a new literature graph."""
        path = self._get_path(graph.id)
        if path.exists():
            raise ValueError(f"LiteratureGraph {graph.id} already exists")

        data = graph.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)

        return graph

    def get(self, graph_id: str) -> Optional[LiteratureGraph]:
        """Get graph by ID."""
        path = self._get_path(graph_id)
        if not path.exists():
            return None

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return LiteratureGraph(**data)

    def update(self, graph: LiteratureGraph) -> LiteratureGraph:
        """Update an existing graph (e.g., v0 -> v1)."""
        path = self._get_path(graph.id)
        if not path.exists():
            raise ValueError(f"LiteratureGraph {graph.id} not found")

        data = graph.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)

        return graph

    def get_by_session(self, session_id: str) -> Optional[LiteratureGraph]:
        """Get the graph for a session (latest version)."""
        graphs = []
        for path in self.base_path.glob("lg_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    graphs.append(LiteratureGraph(**data))
            except Exception:
                continue
        # Return the one with highest version
        graphs.sort(key=lambda g: g.version, reverse=True)
        return graphs[0] if graphs else None


# =============================================================================
# Dual-Graph Storage: StructuredPaper Storage
# =============================================================================


class StructuredPaperStorage:
    """Storage for deep-read structured papers."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "structured_papers"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, paper_id: str) -> Path:
        return self.base_path / f"{paper_id}.json"

    def create(self, paper: StructuredPaper) -> StructuredPaper:
        """Create a new structured paper."""
        path = self._get_path(paper.id)
        if path.exists():
            raise ValueError(f"StructuredPaper {paper.id} already exists")

        data = paper.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)

        return paper

    def get(self, paper_id: str) -> Optional[StructuredPaper]:
        """Get structured paper by ID."""
        path = self._get_path(paper_id)
        if not path.exists():
            return None

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return StructuredPaper(**data)

    def list_by_session(self, session_id: str) -> List[StructuredPaper]:
        """List all structured papers for a session."""
        papers = []
        for path in self.base_path.glob("raw_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    papers.append(StructuredPaper(**data))
            except Exception:
                continue
        return sorted(papers, key=lambda p: p.createdAt, reverse=True)


# =============================================================================
# Dual-Graph Storage: LiteratureMap Storage
# =============================================================================


class LiteratureMapStorage:
    """Storage for literature maps."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "literature_maps"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, map_id: str) -> Path:
        return self.base_path / f"{map_id}.json"

    def create(self, lit_map: LiteratureMap) -> LiteratureMap:
        """Create a new literature map."""
        path = self._get_path(lit_map.id)
        if path.exists():
            raise ValueError(f"LiteratureMap {lit_map.id} already exists")

        data = lit_map.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)

        return lit_map

    def get(self, map_id: str) -> Optional[LiteratureMap]:
        """Get literature map by ID."""
        path = self._get_path(map_id)
        if not path.exists():
            return None

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return LiteratureMap(**data)

    def get_by_session(self, session_id: str) -> Optional[LiteratureMap]:
        """Get the literature map for a session."""
        for path in self.base_path.glob("lm_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    return LiteratureMap(**data)
            except Exception:
                continue
        return None


# =============================================================================
# Dual-Graph Storage: BFTSHandoff Storage
# =============================================================================


class HandoffStorage:
    """Storage for BFTS handoffs."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "handoffs"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, handoff_id: str) -> Path:
        return self.base_path / f"{handoff_id}.json"

    def create(self, handoff: BFTSHandoff) -> BFTSHandoff:
        """Create a new handoff."""
        path = self._get_path(handoff.id)
        if path.exists():
            raise ValueError(f"BFTSHandoff {handoff.id} already exists")

        data = handoff.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)

        return handoff

    def get(self, handoff_id: str) -> Optional[BFTSHandoff]:
        """Get handoff by ID."""
        path = self._get_path(handoff_id)
        if not path.exists():
            return None

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return BFTSHandoff(**data)

    def get_by_session(self, session_id: str) -> Optional[BFTSHandoff]:
        """Get the handoff for a session (latest version)."""
        handoffs = []
        for path in self.base_path.glob("bh_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    handoffs.append(BFTSHandoff(**data))
            except Exception:
                continue
        handoffs.sort(key=lambda h: h.createdAt, reverse=True)
        return handoffs[0] if handoffs else None

    def delete(self, handoff_id: str) -> None:
        """Delete a handoff by ID."""
        path = self._get_path(handoff_id)
        if path.exists():
            os.remove(path)


# =============================================================================
# Dual-Graph Storage: Global Instances
# =============================================================================

_raw_paper_storage: Optional[RawPaperStorage] = None
_graph_storage: Optional[LiteratureGraphStorage] = None
_structured_storage: Optional[StructuredPaperStorage] = None
_map_storage: Optional[LiteratureMapStorage] = None
_handoff_storage: Optional[HandoffStorage] = None


def get_raw_paper_storage() -> RawPaperStorage:
    global _raw_paper_storage
    if _raw_paper_storage is None:
        _raw_paper_storage = RawPaperStorage(_get_data_dir())
    return _raw_paper_storage


def get_literature_graph_storage() -> LiteratureGraphStorage:
    global _graph_storage
    if _graph_storage is None:
        _graph_storage = LiteratureGraphStorage(_get_data_dir())
    return _graph_storage


def get_structured_paper_storage() -> StructuredPaperStorage:
    global _structured_storage
    if _structured_storage is None:
        _structured_storage = StructuredPaperStorage(_get_data_dir())
    return _structured_storage


def get_literature_map_storage() -> LiteratureMapStorage:
    global _map_storage
    if _map_storage is None:
        _map_storage = LiteratureMapStorage(_get_data_dir())
    return _map_storage


def get_handoff_storage() -> HandoffStorage:
    global _handoff_storage
    if _handoff_storage is None:
        _handoff_storage = HandoffStorage(_get_data_dir())
    return _handoff_storage


# =============================================================================
# Phase 2 Storage: ID Generators
# =============================================================================


def generate_reasoning_kg_id() -> str:
    """Generate unique reasoning KG ID."""
    return f"rkg_{uuid.uuid4().hex[:12]}"


def generate_evidence_link_id() -> str:
    """Generate unique evidence link ID."""
    return f"gel_{uuid.uuid4().hex[:12]}"


def generate_path_seed_id() -> str:
    """Generate unique path seed ID."""
    return f"rps_{uuid.uuid4().hex[:12]}"


# =============================================================================
# Phase 2 Storage: ReasoningKG Storage
# =============================================================================


class ReasoningKGStorage:
    """Storage for reasoning knowledge graphs (Graph 2)."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "reasoning_kgs"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, kg_id: str) -> Path:
        return self.base_path / f"{kg_id}.json"

    def create(self, kg: ReasoningKG) -> ReasoningKG:
        path = self._get_path(kg.id)
        if path.exists():
            raise ValueError(f"ReasoningKG {kg.id} already exists")
        data = kg.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)
        return kg

    def get(self, kg_id: str) -> Optional[ReasoningKG]:
        path = self._get_path(kg_id)
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return ReasoningKG(**data)

    def get_by_session(self, session_id: str) -> Optional[ReasoningKG]:
        for path in self.base_path.glob("rkg_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    return ReasoningKG(**data)
            except Exception:
                continue
        return None


# =============================================================================
# Phase 2 Storage: GraphEvidenceLink Storage
# =============================================================================


class GraphEvidenceLinkStorage:
    """Storage for GraphEvidenceLinks between Graph 1 and Graph 2."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "evidence_links"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, link_id: str) -> Path:
        return self.base_path / f"{link_id}.json"

    def create(self, link: GraphEvidenceLink) -> GraphEvidenceLink:
        path = self._get_path(link.linkId)
        if path.exists():
            raise ValueError(f"GraphEvidenceLink {link.linkId} already exists")
        data = link.model_dump()
        _write_json_atomic(path, data, default=str)
        return link

    def get(self, link_id: str) -> Optional[GraphEvidenceLink]:
        path = self._get_path(link_id)
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return GraphEvidenceLink(**data)

    def list_by_session(self, session_id: str) -> List[GraphEvidenceLink]:
        links = []
        for path in self.base_path.glob("gel_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # Evidence links don't have sessionId directly; we load all and filter
                links.append(GraphEvidenceLink(**data))
            except Exception:
                continue
        return links


# =============================================================================
# Phase 2 Storage: PathSeed Storage
# =============================================================================


class PathSeedStorage:
    """Storage for ReasoningPathSeeds."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "path_seeds"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, seed_id: str) -> Path:
        return self.base_path / f"{seed_id}.json"

    def create(self, seed: ReasoningPathSeed) -> ReasoningPathSeed:
        path = self._get_path(seed.seedId)
        if path.exists():
            raise ValueError(f"ReasoningPathSeed {seed.seedId} already exists")
        data = seed.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)
        return seed

    def get(self, seed_id: str) -> Optional[ReasoningPathSeed]:
        path = self._get_path(seed_id)
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return ReasoningPathSeed(**data)

    def list_by_session(self, session_id: str) -> List[ReasoningPathSeed]:
        seeds = []
        for path in self.base_path.glob("rps_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    seeds.append(ReasoningPathSeed(**data))
            except Exception:
                continue
        return sorted(seeds, key=lambda s: (
            s.scores.noveltyPrior + s.scores.feasibilityPrior + s.scores.evidencePrior
        ) if s.scores else 0.0, reverse=True)


# =============================================================================
# Phase 2 Storage: Global Instances
# =============================================================================

_reasoning_kg_storage: Optional[ReasoningKGStorage] = None
_evidence_link_storage: Optional[GraphEvidenceLinkStorage] = None
_path_seed_storage: Optional[PathSeedStorage] = None


def get_reasoning_kg_storage() -> ReasoningKGStorage:
    global _reasoning_kg_storage
    if _reasoning_kg_storage is None:
        _reasoning_kg_storage = ReasoningKGStorage(_get_data_dir())
    return _reasoning_kg_storage


def get_evidence_link_storage() -> GraphEvidenceLinkStorage:
    global _evidence_link_storage
    if _evidence_link_storage is None:
        _evidence_link_storage = GraphEvidenceLinkStorage(_get_data_dir())
    return _evidence_link_storage


def get_path_seed_storage() -> PathSeedStorage:
    global _path_seed_storage
    if _path_seed_storage is None:
        _path_seed_storage = PathSeedStorage(_get_data_dir())
    return _path_seed_storage


# =============================================================================
# Step 6 Storage: RankedIdeaOutput Storage
# =============================================================================


def generate_ranked_output_id() -> str:
    """Generate unique ranked output ID."""
    return f"rio_{uuid.uuid4().hex[:12]}"


class RankedIdeaOutputStorage:
    """Storage for Step 6 ranking output (RankedIdeaOutput)."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "ranked_outputs"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, output_id: str) -> Path:
        return self.base_path / f"{output_id}.json"

    def create(self, ranked_output: RankedIdeaOutput) -> RankedIdeaOutput:
        """Create a new ranked output."""
        path = self._get_path(ranked_output.id)
        if path.exists():
            raise ValueError(f"RankedIdeaOutput {ranked_output.id} already exists")

        data = ranked_output.model_dump()
        # Serialize datetime fields
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        for c in data.get('rankedCandidates', []):
            if c.get('createdAt') and isinstance(c['createdAt'], datetime):
                c['createdAt'] = c['createdAt'].isoformat()
        _write_json_atomic(path, data, default=str)

        return ranked_output

    def get(self, output_id: str) -> Optional[RankedIdeaOutput]:
        """Get ranked output by ID."""
        path = self._get_path(output_id)
        if not path.exists():
            return None

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        for c in data.get('rankedCandidates', []):
            if c.get('createdAt') and isinstance(c['createdAt'], str):
                c['createdAt'] = datetime.fromisoformat(c['createdAt'])
        return RankedIdeaOutput(**data)

    def get_by_session(self, session_id: str) -> Optional[RankedIdeaOutput]:
        """Get the ranked output for a session (most recent if multiple)."""
        outputs = []
        for path in self.base_path.glob("rio_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    for c in data.get('rankedCandidates', []):
                        if c.get('createdAt') and isinstance(c['createdAt'], str):
                            c['createdAt'] = datetime.fromisoformat(c['createdAt'])
                    outputs.append(RankedIdeaOutput(**data))
            except Exception:
                continue
        outputs.sort(key=lambda o: o.createdAt, reverse=True)
        return outputs[0] if outputs else None


_ranked_output_storage: Optional[RankedIdeaOutputStorage] = None


def get_ranked_output_storage() -> RankedIdeaOutputStorage:
    global _ranked_output_storage
    if _ranked_output_storage is None:
        _ranked_output_storage = RankedIdeaOutputStorage(_get_data_dir())
    return _ranked_output_storage


# =============================================================================
# Step 5 Storage: IdeaSearchTree, ProbeLiterature, GraphPatch (PDF v5)
# =============================================================================


def generate_search_tree_id() -> str:
    """Generate unique search tree ID."""
    return f"ist_{uuid.uuid4().hex[:12]}"


def generate_probe_result_id() -> str:
    """Generate unique literature probe result ID."""
    return f"lpr_{uuid.uuid4().hex[:12]}"


def generate_graph_patch_id() -> str:
    """Generate unique graph patch ID."""
    return f"gp_{uuid.uuid4().hex[:12]}"


class IdeaSearchTreeStorage:
    """Storage for BFTS idea search trees (PDF v5 section 7.10)."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "search_trees"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, tree_id: str) -> Path:
        return self.base_path / f"{tree_id}.json"

    def create(self, tree: IdeaSearchTree) -> IdeaSearchTree:
        path = self._get_path(tree.id)
        if path.exists():
            raise ValueError(f"IdeaSearchTree {tree.id} already exists")

        data = tree.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)
        return tree

    def get(self, tree_id: str) -> Optional[IdeaSearchTree]:
        path = self._get_path(tree_id)
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return IdeaSearchTree(**data)

    def get_by_session(self, session_id: str) -> Optional[IdeaSearchTree]:
        trees = []
        for path in self.base_path.glob("ist_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    trees.append(IdeaSearchTree(**data))
            except Exception:
                continue
        trees.sort(key=lambda t: t.createdAt, reverse=True)
        return trees[0] if trees else None


class ProbeLiteratureStorage:
    """Storage for literature probe results (PDF v5 section 7.10)."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "probe_results"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, result_id: str) -> Path:
        return self.base_path / f"{result_id}.json"

    def create(self, result: LiteratureProbeResult) -> LiteratureProbeResult:
        path = self._get_path(result.id)
        data = result.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)
        return result

    def get(self, result_id: str) -> Optional[LiteratureProbeResult]:
        path = self._get_path(result_id)
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return LiteratureProbeResult(**data)

    def list_by_session(self, session_id: str) -> List[LiteratureProbeResult]:
        results = []
        for path in self.base_path.glob("lpr_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    results.append(LiteratureProbeResult(**data))
            except Exception:
                continue
        return sorted(results, key=lambda r: r.createdAt, reverse=True)

    def list_by_node(self, node_id: str) -> List[LiteratureProbeResult]:
        results = []
        for path in self.base_path.glob("lpr_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('nodeId') == node_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    results.append(LiteratureProbeResult(**data))
            except Exception:
                continue
        return sorted(results, key=lambda r: r.createdAt, reverse=True)


class GraphPatchStorage:
    """Storage for graph patches applied during BFTS (PDF v5 section 7.10)."""

    def __init__(self, data_dir: str = "data"):
        self.base_path = Path(data_dir) / "ideas" / "graph_patches"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, patch_id: str) -> Path:
        return self.base_path / f"{patch_id}.json"

    def create(self, patch: GraphPatch) -> GraphPatch:
        path = self._get_path(patch.id)
        data = patch.model_dump()
        data['createdAt'] = data['createdAt'].isoformat() if isinstance(data['createdAt'], datetime) else data['createdAt']
        _write_json_atomic(path, data, default=str)
        return patch

    def get(self, patch_id: str) -> Optional[GraphPatch]:
        path = self._get_path(patch_id)
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if data.get('createdAt') and isinstance(data['createdAt'], str):
            data['createdAt'] = datetime.fromisoformat(data['createdAt'])
        return GraphPatch(**data)

    def list_by_session(self, session_id: str) -> List[GraphPatch]:
        patches = []
        for path in self.base_path.glob("gp_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('sessionId') == session_id:
                    if data.get('createdAt') and isinstance(data['createdAt'], str):
                        data['createdAt'] = datetime.fromisoformat(data['createdAt'])
                    patches.append(GraphPatch(**data))
            except Exception:
                continue
        return sorted(patches, key=lambda p: p.createdAt, reverse=True)


# Singleton instances
_search_tree_storage: Optional[IdeaSearchTreeStorage] = None
_probe_literature_storage: Optional[ProbeLiteratureStorage] = None
_graph_patch_storage: Optional[GraphPatchStorage] = None


def get_search_tree_storage() -> IdeaSearchTreeStorage:
    global _search_tree_storage
    if _search_tree_storage is None:
        _search_tree_storage = IdeaSearchTreeStorage(_get_data_dir())
    return _search_tree_storage


def get_probe_literature_storage() -> ProbeLiteratureStorage:
    global _probe_literature_storage
    if _probe_literature_storage is None:
        _probe_literature_storage = ProbeLiteratureStorage(_get_data_dir())
    return _probe_literature_storage


def get_graph_patch_storage() -> GraphPatchStorage:
    global _graph_patch_storage
    if _graph_patch_storage is None:
        _graph_patch_storage = GraphPatchStorage(_get_data_dir())
    return _graph_patch_storage
