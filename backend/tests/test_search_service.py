import json
import time
import urllib.error

from app.services import search_service as search_service_module
from app.services.search_service import LocalCorpusSearch, OpenAlexSearch, SemanticScholarSearch


def test_semantic_scholar_circuit_breaks_after_rate_limit(monkeypatch):
    client = SemanticScholarSearch()
    client.min_request_interval = 0.0
    calls = {"count": 0}

    def fake_urlopen(*args, **kwargs):
        calls["count"] += 1
        raise urllib.error.HTTPError(
            url="https://api.semanticscholar.org/graph/v1/paper/search",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )

    monkeypatch.setattr(search_service_module, "_urlopen", fake_urlopen)
    monkeypatch.setattr(search_service_module.time, "sleep", lambda *_args, **_kwargs: None)

    assert client.search("citation faithful RAG", limit=1) == []
    assert client.disabled_until > time.time()
    assert client.search("citation faithful RAG", limit=1) == []
    assert calls["count"] == 2


def test_local_corpus_matches_equivalent_spaced_cjk_terms():
    client = LocalCorpusSearch()
    paper = {
        "title": "\u7ea2\u697c\u68a6 \u4eba\u7269\u5173\u7cfb \u7f51\u7edc\u5206\u6790",
        "abstract": "\u7814\u7a76\u7ea2\u697c\u68a6\u4e2d\u7684\u4eba\u7269\u5173\u7cfb\u4e0e\u793e\u4f1a\u7f51\u7edc\u3002",
        "keywords": ["\u7ea2\u697c\u68a6", "\u4eba\u7269\u5173\u7cfb", "\u7f51\u7edc\u5206\u6790"],
    }

    assert client._compute_relevance(paper, "\u7ea2\u697c\u68a6\u4eba\u7269\u5173\u7cfb\u7f51\u7edc\u5206\u6790") >= 0.18


def test_openalex_does_not_treat_provider_rank_as_normalized_relevance(monkeypatch):
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "title": "Unrelated high-ranked result",
                "publication_year": 2024,
                "relevance_score": 1223.5,
                "authorships": [],
            }
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(search_service_module, "_urlopen", lambda *_args, **_kwargs: FakeResponse())

    results = OpenAlexSearch().search("Dream of the Red Chamber ending", limit=1)

    assert results[0].relevance_score == 0.0
