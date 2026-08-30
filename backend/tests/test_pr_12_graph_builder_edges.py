"""
PR-B12: graph_builder dependency_map validates edge endpoints.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.faros.models.blueprint import Blueprint, WorkflowNode, WorkflowEdge
from app.faros.runtime.graph_builder import GraphBuilder


def _make_node(nid):
    return WorkflowNode(id=nid, capability=nid, name=nid)


class TestDependencyMapEdgeValidation:

    def setup_method(self):
        self.builder = GraphBuilder()

    def test_valid_edges(self):
        bp = Blueprint(
            id="bp1", name="test", version="1",
            workflow=[_make_node("a"), _make_node("b"), _make_node("c")],
            edges=[
                WorkflowEdge(**{"from": "a", "to": "b"}),
                WorkflowEdge(**{"from": "b", "to": "c"}),
            ],
        )
        result = self.builder.dependency_map(bp)
        assert result["a"]["downstream"] == ["b"]
        assert result["b"]["upstream"] == ["a"]
        assert result["b"]["downstream"] == ["c"]
        assert result["c"]["upstream"] == ["b"]

    def test_edge_with_nonexistent_source_raises(self):
        bp = Blueprint(
            id="bp2", name="test", version="1",
            workflow=[_make_node("a")],
            edges=[WorkflowEdge(**{"from": "ghost", "to": "a"})],
        )
        with pytest.raises(ValueError, match="non-existent node"):
            self.builder.dependency_map(bp)

    def test_edge_with_nonexistent_target_raises(self):
        bp = Blueprint(
            id="bp3", name="test", version="1",
            workflow=[_make_node("a")],
            edges=[WorkflowEdge(**{"from": "a", "to": "ghost"})],
        )
        with pytest.raises(ValueError, match="non-existent node"):
            self.builder.dependency_map(bp)

    def test_edge_with_both_endpoints_missing_raises(self):
        bp = Blueprint(
            id="bp4", name="test", version="1",
            workflow=[_make_node("a")],
            edges=[WorkflowEdge(**{"from": "x", "to": "y"})],
        )
        with pytest.raises(ValueError, match="non-existent node"):
            self.builder.dependency_map(bp)

    def test_no_edges(self):
        bp = Blueprint(
            id="bp5", name="test", version="1",
            workflow=[_make_node("a"), _make_node("b")],
            edges=[],
        )
        result = self.builder.dependency_map(bp)
        assert result["a"]["upstream"] == []
        assert result["a"]["downstream"] == []
