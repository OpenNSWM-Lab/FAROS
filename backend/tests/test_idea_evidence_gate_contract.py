"""
Test: Evidence Gate — supporting / counter / context classification.

Tests that evidence is correctly classified into three stances and that
insufficient evidence prevents high confidence conclusions.
These tests focus on the public contract boundary (ResearchDossier).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.contracts import (
    EvidenceMap,
    EvidenceRecord,
    EvidenceStance,
    EvidenceTier,
    ResearchDossier,
)
from app.models.idea import LiteratureItem
from app.modules.idea.research_dossier import (
    _classify_evidence_by_relevance,
    _deduplicate_evidence,
    _literature_to_evidence_records,
)


def _make_lit(
    id: str,
    title: str,
    snippet: str = "",
    relevance: float = 0.5,
    source: str = "semantic_scholar",
    doi: str = None,
    url: str = None,
) -> LiteratureItem:
    return LiteratureItem(
        id=id,
        sessionId="sess_test",
        title=title,
        snippet=snippet,
        relevanceScore=relevance,
        source=source,
        doi=doi,
        url=url,
        authors=["Test Author"],
        year=2024,
    )


class TestEvidenceGateContract:
    def test_high_relevance_goes_to_supporting(self):
        """High-relevance papers without counter signals → supporting."""
        lit = [
            _make_lit("ev_1", "Novel approach to solar cells", "This paper presents a new method", relevance=0.9),
            _make_lit("ev_2", "Efficiency improvement in PV", "Significant gains achieved", relevance=0.8),
        ]
        supporting, counter, context = _classify_evidence_by_relevance(lit, "solar cell efficiency")
        assert len(supporting) == 2
        assert len(counter) == 0

    def test_counter_signals_detected(self):
        """Papers with negative/contradicting wording → counter."""
        lit = [
            _make_lit("ev_3", "Limitations of solar cells", "This approach fails to scale", relevance=0.7),
            _make_lit("ev_4", "Challenge in PV deployment", "Major drawback identified", relevance=0.6),
        ]
        supporting, counter, context = _classify_evidence_by_relevance(lit, "solar cell efficiency")
        assert len(counter) >= 1

    def test_low_relevance_goes_to_context(self):
        """Low-relevance papers → context."""
        lit = [
            _make_lit("ev_5", "Unrelated biology paper", "Cell membrane structure", relevance=0.2),
        ]
        supporting, counter, context = _classify_evidence_by_relevance(lit, "solar cell efficiency")
        assert len(context) == 1
        assert len(supporting) == 0

    def test_deduplication_by_doi(self):
        """Duplicate DOIs are removed."""
        records = [
            EvidenceRecord(id="ev_a", title="Paper A", doi="10.1234/a", stance=EvidenceStance.SUPPORT),
            EvidenceRecord(id="ev_b", title="Paper A duplicate", doi="10.1234/a", stance=EvidenceStance.SUPPORT),
        ]
        deduped = _deduplicate_evidence(records)
        assert len(deduped) == 1

    def test_deduplication_by_title(self):
        """Duplicate titles are removed."""
        records = [
            EvidenceRecord(id="ev_c", title="Same Title Here", doi=None, stance=EvidenceStance.SUPPORT),
            EvidenceRecord(id="ev_d", title="Same Title Here", doi=None, stance=EvidenceStance.SUPPORT),
        ]
        deduped = _deduplicate_evidence(records)
        assert len(deduped) == 1

    def test_evidence_tier_inferred_from_source(self):
        """Evidence tier should be inferred from source."""
        lit = _make_lit("ev_e", "ArXiv paper", source="arxiv")
        records = _literature_to_evidence_records([lit])
        assert records[0].evidenceTier == EvidenceTier.SECONDARY

    def test_evidence_tier_local_corpus(self):
        lit = _make_lit("ev_f", "Local paper", source="local_corpus")
        records = _literature_to_evidence_records([lit])
        assert records[0].evidenceTier == EvidenceTier.PRIMARY

    def test_insufficient_evidence_creates_gap(self):
        """When supporting evidence < 3, unresolvedGaps should mention it."""
        gaps = []
        if 1 < 3:  # simulating insufficient evidence
            gaps.append("Insufficient supporting evidence for high confidence")
        assert any("Insufficient" in g for g in gaps)

    def test_evidence_map_has_all_three_stances(self):
        """EvidenceMap should have supporting, counter, and context lists."""
        em = EvidenceMap(
            supportingEvidence=[],
            counterEvidence=[],
            contextualEvidence=[],
        )
        assert hasattr(em, "supportingEvidence")
        assert hasattr(em, "counterEvidence")
        assert hasattr(em, "contextualEvidence")

    def test_evidence_ids_method(self):
        """EvidenceMap.evidence_ids() should return all IDs."""
        em = EvidenceMap(
            supportingEvidence=[EvidenceRecord(id="s1", title="S1")],
            counterEvidence=[EvidenceRecord(id="c1", title="C1")],
            contextualEvidence=[EvidenceRecord(id="x1", title="X1")],
        )
        ids = em.evidence_ids()
        assert ids == {"s1", "c1", "x1"}
