"""
Search Service for Literature Discovery

Provides multiple search backends:
1. Semantic Scholar API (free, no key required for basic usage)
2. Local corpus search (for offline/testing)
3. ArXiv API (free, no key required)

Falls back gracefully when APIs are unavailable.
"""

import json
import logging
import os
import re
import ssl
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import urllib.request
import urllib.parse
import urllib.error

logger = logging.getLogger(__name__)


def _build_ssl_context() -> ssl.SSLContext:
    """Use certifi when available because some Windows Python installs lack CA paths."""
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CONTEXT = _build_ssl_context()
_INSECURE_SSL_CONTEXT = ssl._create_unverified_context()
_USER_AGENT = os.getenv(
    "FAROS_PAPER_SEARCH_USER_AGENT",
    "FAROS/0.1 literature-search",
)


def _urlopen(
    request: urllib.request.Request,
    timeout: int,
    *,
    context: ssl.SSLContext = _SSL_CONTEXT,
):
    """Open URLs with a stable TLS context and a consistent user agent."""
    if not request.has_header("User-agent"):
        request.add_header("User-Agent", _USER_AGENT)
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    authors: List[str]
    abstract: str
    year: Optional[int]
    venue: Optional[str]
    url: Optional[str]
    doi: Optional[str]
    arxiv_id: Optional[str]
    citation_count: Optional[int]
    source: str  # "semantic_scholar", "arxiv", "local"
    relevance_score: float = 0.0


class SemanticScholarSearch:
    """
    Semantic Scholar API client.
    
    Free tier allows 100 requests per 5 minutes without API key.
    """
    
    BASE_URL = "https://api.semanticscholar.org/graph/v1"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.last_request_time = 0
        self.disabled_until = 0.0
        # Unauthenticated free tier is 100 requests / 5 minutes. Keep a small
        # margin so multi-query idea sessions do not immediately hit 429.
        self.min_request_interval = 1.0 if api_key else 3.2
    
    def _rate_limit(self):
        """Ensure we don't exceed rate limits."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()
    
    def _make_request(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict]:
        """Make a request to Semantic Scholar API."""
        if time.time() < self.disabled_until:
            logger.info("Semantic Scholar temporarily disabled after a recent connection failure")
            return None

        self._rate_limit()
        
        url = f"{self.BASE_URL}/{endpoint}"
        query_string = urllib.parse.urlencode(params)
        full_url = f"{url}?{query_string}"
        
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        for attempt in range(2):
            try:
                request = urllib.request.Request(full_url, headers=headers)
                with _urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 0:
                    retry_after = e.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after else self.min_request_interval * 2
                    except ValueError:
                        delay = self.min_request_interval * 2
                    logger.warning("Semantic Scholar rate limited; retrying in %.1fs", delay)
                    time.sleep(delay)
                    continue
                logger.warning(f"Semantic Scholar API error: {e.code} - {e.reason}")
                return None
            except urllib.error.URLError as e:
                logger.warning(f"Semantic Scholar connection error: {e.reason}")
                self.disabled_until = time.time() + 120
                return None
            except Exception as e:
                logger.warning(f"Semantic Scholar request failed: {e}")
                self.disabled_until = time.time() + 120
                return None
        return None
    
    def search(self, query: str, limit: int = 10, year_range: Optional[tuple] = None) -> List[SearchResult]:
        """
        Search for papers by keyword.
        
        Args:
            query: Search query
            limit: Maximum results
            year_range: Optional (start_year, end_year) tuple
            
        Returns:
            List of SearchResult objects
        """
        params = {
            "query": query,
            "limit": min(limit, 100),
            "fields": "title,authors,abstract,year,venue,url,externalIds,citationCount"
        }
        
        if year_range:
            params["year"] = f"{year_range[0]}-{year_range[1]}"
        
        data = self._make_request("paper/search", params)
        if not data or "data" not in data:
            return []
        
        results = []
        for paper in data.get("data", []):
            if not paper.get("title"):
                continue
            
            authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
            external_ids = paper.get("externalIds", {}) or {}
            
            results.append(SearchResult(
                title=paper.get("title", ""),
                authors=authors[:10],  # Limit authors
                abstract=paper.get("abstract", "") or "",
                year=paper.get("year"),
                venue=paper.get("venue"),
                url=paper.get("url"),
                doi=external_ids.get("DOI"),
                arxiv_id=external_ids.get("ArXiv"),
                citation_count=paper.get("citationCount"),
                source="semantic_scholar"
            ))
        
        return results


class ArxivSearch:
    """
    ArXiv API client.
    
    Completely free, no API key required.
    """
    
    BASE_URL = "https://export.arxiv.org/api/query"
    HTTP_FALLBACK_URL = "http://export.arxiv.org/api/query"
    
    def __init__(self):
        self.last_request_time = 0
        self.min_request_interval = 3.0  # ArXiv recommends 3 second delay
    
    def _rate_limit(self):
        """Ensure we don't exceed rate limits."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()
    
    def _parse_arxiv_response(self, xml_text: str) -> List[SearchResult]:
        """Parse ArXiv XML response."""
        results = []
        
        # Simple XML parsing without external dependencies
        entries = re.findall(r'<entry>(.*?)</entry>', xml_text, re.DOTALL)
        
        for entry in entries:
            title_match = re.search(r'<title>(.*?)</title>', entry, re.DOTALL)
            abstract_match = re.search(r'<summary>(.*?)</summary>', entry, re.DOTALL)
            published_match = re.search(r'<published>(.*?)</published>', entry)
            id_match = re.search(r'<id>(.*?)</id>', entry)
            
            title = title_match.group(1).strip().replace('\n', ' ') if title_match else ""
            abstract = abstract_match.group(1).strip().replace('\n', ' ') if abstract_match else ""
            
            # Extract authors
            authors = re.findall(r'<author>.*?<name>(.*?)</name>.*?</author>', entry, re.DOTALL)
            
            # Extract year from published date
            year = None
            if published_match:
                try:
                    year = int(published_match.group(1)[:4])
                except (ValueError, IndexError):
                    pass
            
            # Extract arxiv ID
            arxiv_id = None
            url = None
            if id_match:
                url = id_match.group(1).strip()
                arxiv_match = re.search(r'arxiv.org/abs/(.+)$', url)
                if arxiv_match:
                    arxiv_id = arxiv_match.group(1)
            
            if title:
                results.append(SearchResult(
                    title=title,
                    authors=authors[:10],
                    abstract=abstract,
                    year=year,
                    venue="arXiv",
                    url=url,
                    doi=None,
                    arxiv_id=arxiv_id,
                    citation_count=None,
                    source="arxiv"
                ))
        
        return results

    def _build_query_variants(self, query: str) -> List[str]:
        """Build arXiv query variants from strict to broad."""
        raw = " ".join(query.split())
        normalized = raw.lower()
        variants: List[str] = []

        phrase_parts: List[str] = []
        consumed_terms: set[str] = set()
        known_phrases = [
            "retrieval augmented generation",
            "large language model",
            "uncertainty quantification",
            "citation faithfulness",
            "chain of thought",
        ]
        for phrase in known_phrases:
            if phrase in normalized:
                phrase_parts.append(f'all:"{phrase}"')
                consumed_terms.update(phrase.split())

        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", normalized)
        stopwords = {
            "and", "the", "for", "with", "from", "that", "this", "into",
            "using", "based", "how", "can", "are", "what", "when", "where",
            "does", "improve", "improving", "generation",
        }
        signal_priority = [
            "faithfulness", "citation", "uncertainty", "hallucination",
            "grounding", "factuality", "attribution", "calibration",
            "retrieval", "augmented", "gating", "confidence",
        ]
        unique_tokens: List[str] = []
        for token in tokens:
            if token in stopwords or token in consumed_terms:
                continue
            if token not in unique_tokens:
                unique_tokens.append(token)

        ranked_tokens = sorted(
            unique_tokens,
            key=lambda token: (
                signal_priority.index(token) if token in signal_priority else len(signal_priority),
                unique_tokens.index(token),
            ),
        )

        if phrase_parts:
            for token_count in (1, 2):
                selected = ranked_tokens[:token_count]
                if selected:
                    variants.append(" AND ".join(phrase_parts + [f"all:{token}" for token in selected]))
            variants.append(" AND ".join(phrase_parts))

        if ranked_tokens:
            variants.append(" AND ".join(f"all:{token}" for token in ranked_tokens[:4]))

        variants.append(f"all:{raw}")

        deduped: List[str] = []
        for variant in variants:
            if variant and variant not in deduped:
                deduped.append(variant)
        return deduped
    
    def search(self, query: str, limit: int = 10, categories: Optional[List[str]] = None) -> List[SearchResult]:
        """
        Search ArXiv for papers.
        
        Args:
            query: Search query
            limit: Maximum results
            categories: Optional list of ArXiv categories (e.g., ["cs.AI", "cs.LG"])
            
        Returns:
            List of SearchResult objects
        """
        self._rate_limit()
        
        last_error: Optional[Exception] = None
        for variant in self._build_query_variants(query):
            search_query = variant
            if categories:
                cat_query = " OR ".join([f"cat:{cat}" for cat in categories])
                search_query = f"({search_query}) AND ({cat_query})"

            params = {
                "search_query": search_query,
                "start": 0,
                "max_results": min(limit, 100),
                "sortBy": "relevance",
                "sortOrder": "descending"
            }

            query_string = urllib.parse.urlencode(params)
            urls = [
                f"{self.BASE_URL}?{query_string}",
                f"{self.HTTP_FALLBACK_URL}?{query_string}",
            ]

            for attempt, url in enumerate(urls, start=1):
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
                    with _urlopen(request, timeout=30) as response:
                        xml_text = response.read().decode('utf-8')
                        results = self._parse_arxiv_response(xml_text)
                        if results:
                            return results
                except Exception as e:
                    last_error = e
                    if (
                        url.startswith("https://")
                        and _env_bool("FAROS_ALLOW_INSECURE_ARXIV_SSL", True)
                        and "CERTIFICATE_VERIFY_FAILED" in str(e)
                    ):
                        try:
                            request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
                            with _urlopen(request, timeout=30, context=_INSECURE_SSL_CONTEXT) as response:
                                xml_text = response.read().decode('utf-8')
                                results = self._parse_arxiv_response(xml_text)
                                if results:
                                    logger.warning(
                                        "ArXiv HTTPS succeeded with insecure TLS fallback; "
                                        "fix local CA certificates for production use."
                                    )
                                    return results
                        except Exception as fallback_error:
                            last_error = fallback_error
                    logger.warning("ArXiv search attempt %s failed: %s", attempt, e)
                    time.sleep(1.0)

        logger.warning(f"ArXiv search failed or returned no results: {last_error}")
        return []


class LocalCorpusSearch:
    """
    Local corpus search for offline/testing.
    
    Uses a pre-defined set of papers for demonstration.
    """
    
    # Sample papers for different research areas
    SAMPLE_PAPERS = [
        {
            "title": "Attention Is All You Need",
            "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar", "Jakob Uszkoreit"],
            "abstract": "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder. The best performing models also connect the encoder and decoder through an attention mechanism. We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.",
            "year": 2017,
            "venue": "NeurIPS",
            "arxiv_id": "1706.03762",
            "keywords": ["transformer", "attention", "neural network", "nlp", "deep learning"]
        },
        {
            "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
            "authors": ["Jacob Devlin", "Ming-Wei Chang", "Kenton Lee", "Kristina Toutanova"],
            "abstract": "We introduce a new language representation model called BERT, which stands for Bidirectional Encoder Representations from Transformers. Unlike recent language representation models, BERT is designed to pre-train deep bidirectional representations from unlabeled text by jointly conditioning on both left and right context in all layers.",
            "year": 2019,
            "venue": "NAACL",
            "arxiv_id": "1810.04805",
            "keywords": ["bert", "transformer", "nlp", "pre-training", "language model"]
        },
        {
            "title": "Language Models are Few-Shot Learners",
            "authors": ["Tom Brown", "Benjamin Mann", "Nick Ryder", "Melanie Subbiah"],
            "abstract": "Recent work has demonstrated substantial gains on many NLP tasks and benchmarks by pre-training on a large corpus of text followed by fine-tuning on a specific task. We demonstrate that scaling up language models greatly improves task-agnostic, few-shot performance.",
            "year": 2020,
            "venue": "NeurIPS",
            "arxiv_id": "2005.14165",
            "keywords": ["gpt-3", "language model", "few-shot", "nlp", "scaling"]
        },
        {
            "title": "Deep Residual Learning for Image Recognition",
            "authors": ["Kaiming He", "Xiangyu Zhang", "Shaoqing Ren", "Jian Sun"],
            "abstract": "Deeper neural networks are more difficult to train. We present a residual learning framework to ease the training of networks that are substantially deeper than those used previously. We explicitly reformulate the layers as learning residual functions with reference to the layer inputs.",
            "year": 2016,
            "venue": "CVPR",
            "arxiv_id": "1512.03385",
            "keywords": ["resnet", "deep learning", "computer vision", "image recognition", "residual"]
        },
        {
            "title": "Generative Adversarial Networks",
            "authors": ["Ian Goodfellow", "Jean Pouget-Abadie", "Mehdi Mirza", "Bing Xu"],
            "abstract": "We propose a new framework for estimating generative models via an adversarial process, in which we simultaneously train two models: a generative model G that captures the data distribution, and a discriminative model D that estimates the probability that a sample came from the training data rather than G.",
            "year": 2014,
            "venue": "NeurIPS",
            "arxiv_id": "1406.2661",
            "keywords": ["gan", "generative model", "deep learning", "adversarial", "neural network"]
        },
        {
            "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
            "authors": ["Patrick Lewis", "Ethan Perez", "Aleksandra Piktus", "Fabio Petroni"],
            "abstract": "Large pre-trained language models have been shown to store factual knowledge in their parameters, and achieve state-of-the-art results when fine-tuned on downstream NLP tasks. However, their ability to access and precisely manipulate knowledge is still limited. We explore a general-purpose fine-tuning recipe for retrieval-augmented generation (RAG).",
            "year": 2020,
            "venue": "NeurIPS",
            "arxiv_id": "2005.11401",
            "keywords": ["rag", "retrieval", "generation", "nlp", "knowledge"]
        },
        {
            "title": "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models",
            "authors": ["Jason Wei", "Xuezhi Wang", "Dale Schuurmans", "Maarten Bosma"],
            "abstract": "We explore how generating a chain of thought—a series of intermediate reasoning steps—significantly improves the ability of large language models to perform complex reasoning. In particular, we show how such reasoning abilities emerge naturally in sufficiently large language models.",
            "year": 2022,
            "venue": "NeurIPS",
            "arxiv_id": "2201.11903",
            "keywords": ["chain-of-thought", "reasoning", "llm", "prompting", "language model"]
        },
        {
            "title": "Constitutional AI: Harmlessness from AI Feedback",
            "authors": ["Yuntao Bai", "Saurav Kadavath", "Sandipan Kundu", "Amanda Askell"],
            "abstract": "As AI systems become more capable, we would like to enlist their help to supervise other AIs. We experiment with methods for training a harmless AI assistant through self-improvement, without any human labels identifying harmful outputs.",
            "year": 2022,
            "venue": "arXiv",
            "arxiv_id": "2212.08073",
            "keywords": ["constitutional ai", "alignment", "safety", "harmless", "ai feedback"]
        },
        {
            "title": "Training language models to follow instructions with human feedback",
            "authors": ["Long Ouyang", "Jeff Wu", "Xu Jiang", "Diogo Almeida"],
            "abstract": "Making language models bigger does not inherently make them better at following a user's intent. We show an avenue for aligning language models with user intent on a wide range of tasks by fine-tuning with human feedback.",
            "year": 2022,
            "venue": "NeurIPS",
            "arxiv_id": "2203.02155",
            "keywords": ["instructgpt", "rlhf", "alignment", "instruction following", "human feedback"]
        },
        {
            "title": "Scaling Laws for Neural Language Models",
            "authors": ["Jared Kaplan", "Sam McCandlish", "Tom Henighan", "Tom Brown"],
            "abstract": "We study empirical scaling laws for language model performance on the cross-entropy loss. The loss scales as a power-law with model size, dataset size, and the amount of compute used for training, with some trends spanning more than seven orders of magnitude.",
            "year": 2020,
            "venue": "arXiv",
            "arxiv_id": "2001.08361",
            "keywords": ["scaling laws", "language model", "compute", "training", "neural network"]
        }
    ]
    
    def __init__(self, corpus_path: Optional[str] = None):
        """
        Initialize local corpus search.
        
        Args:
            corpus_path: Optional path to JSON file with additional papers
        """
        self.papers = list(self.SAMPLE_PAPERS)
        
        if corpus_path:
            try:
                with open(corpus_path, 'r') as f:
                    additional = json.load(f)
                    if isinstance(additional, list):
                        self.papers.extend(additional)
            except Exception as e:
                logger.warning(f"Failed to load corpus from {corpus_path}: {e}")
    
    def _query_terms(self, query: str) -> List[str]:
        """Extract topic-bearing query terms for strict offline fallback search."""
        normalized = query.lower().replace("-", " ")
        if "rag" in normalized:
            normalized = f"{normalized} retrieval augmented generation"
        stopwords = {
            "and", "the", "for", "with", "from", "that", "this", "into",
            "using", "based", "how", "can", "are", "what", "when", "where",
            "does", "method", "methods", "approach", "paper", "study",
            "research", "improve", "improving", "generation", "language",
            "model", "models", "large", "learning", "neural",
        }
        terms: List[str] = []
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", normalized):
            if token in stopwords:
                continue
            if token not in terms:
                terms.append(token)
        return terms

    def _compute_relevance(self, paper: Dict, query: str) -> float:
        """Compute strict local relevance so generic sample papers do not pollute evidence."""
        query_terms = set(self._query_terms(query))
        if not query_terms:
            return 0.0

        title = paper.get("title", "").lower().replace("-", " ")
        abstract = paper.get("abstract", "").lower().replace("-", " ")
        keywords_text = " ".join(str(k).lower().replace("-", " ") for k in paper.get("keywords", []))
        title_terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", title))
        keyword_terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", keywords_text))
        abstract_terms = set(re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", abstract))

        title_hits = len(query_terms & title_terms)
        keyword_hits = len(query_terms & keyword_terms)
        abstract_hits = len(query_terms & abstract_terms)
        total_hits = title_hits + keyword_hits + abstract_hits
        if total_hits == 0:
            return 0.0

        denominator = max(len(query_terms), 1)
        title_overlap = title_hits / denominator
        keyword_overlap = keyword_hits / denominator
        abstract_overlap = abstract_hits / denominator

        phrase_bonus = 0.0
        combined_text = f"{title} {abstract} {keywords_text}"
        for phrase in [
            "retrieval augmented generation",
            "citation faithfulness",
            "uncertainty estimation",
            "hallucination detection",
            "human feedback",
        ]:
            if phrase in query.lower().replace("-", " ") and phrase in combined_text:
                phrase_bonus += 0.12

        score = 0.45 * title_overlap + 0.35 * keyword_overlap + 0.20 * abstract_overlap + phrase_bonus

        # If a local paper only matches generic remnants, cap it below the default threshold.
        strong_query_terms = query_terms - {
            "retrieval", "augmented", "transformer", "attention", "data", "task", "tasks",
        }
        strong_hits = len(strong_query_terms & (title_terms | keyword_terms | abstract_terms))
        if strong_query_terms and strong_hits == 0:
            return min(score, 0.09)
        return min(score, 1.0)
    
    def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        """
        Search local corpus.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of SearchResult objects
        """
        min_relevance = float(os.getenv("FAROS_LOCAL_MIN_RELEVANCE", "0.18"))
        scored_papers = []
        for paper in self.papers:
            score = self._compute_relevance(paper, query)
            if score >= min_relevance:
                scored_papers.append((score, paper))
        
        # Sort by relevance
        scored_papers.sort(key=lambda x: x[0], reverse=True)
        
        results = []
        for score, paper in scored_papers[:limit]:
            results.append(SearchResult(
                title=paper.get("title", ""),
                authors=paper.get("authors", []),
                abstract=paper.get("abstract", ""),
                year=paper.get("year"),
                venue=paper.get("venue"),
                url=f"https://arxiv.org/abs/{paper.get('arxiv_id')}" if paper.get("arxiv_id") else None,
                doi=paper.get("doi"),
                arxiv_id=paper.get("arxiv_id"),
                citation_count=paper.get("citation_count"),
                source="local",
                relevance_score=score
            ))
        
        return results


class SearchService:
    """
    Unified search service with fallback support.
    
    Tries multiple backends in order:
    1. Semantic Scholar (if available)
    2. ArXiv (always available)
    3. Local corpus (always available)
    """
    
    def __init__(
        self,
        semantic_scholar_key: Optional[str] = None,
        local_corpus_path: Optional[str] = None,
        use_semantic_scholar: bool = True,
        use_arxiv: bool = True,
        use_local: bool = True
    ):
        self.semantic_scholar = SemanticScholarSearch(semantic_scholar_key) if use_semantic_scholar else None
        self.arxiv = ArxivSearch() if use_arxiv else None
        self.local = LocalCorpusSearch(local_corpus_path) if use_local else None
    
    def search(
        self,
        query: str,
        limit: int = 10,
        sources: Optional[List[str]] = None
    ) -> List[SearchResult]:
        """
        Search for papers across all available sources.
        
        Args:
            query: Search query
            limit: Maximum results per source
            sources: Optional list of sources to use ["semantic_scholar", "arxiv", "local"]
            
        Returns:
            Combined list of SearchResult objects, deduplicated by title
        """
        all_results = []
        sources = sources or ["semantic_scholar", "arxiv", "local"]
        
        # Try Semantic Scholar
        if "semantic_scholar" in sources and self.semantic_scholar:
            try:
                results = self.semantic_scholar.search(query, limit)
                all_results.extend(results)
                logger.info(f"Semantic Scholar returned {len(results)} results")
            except Exception as e:
                logger.warning(f"Semantic Scholar search failed: {e}")
        
        # Try ArXiv
        if "arxiv" in sources and self.arxiv:
            try:
                results = self.arxiv.search(query, limit)
                all_results.extend(results)
                logger.info(f"ArXiv returned {len(results)} results")
            except Exception as e:
                logger.warning(f"ArXiv search failed: {e}")
        
        external_results_count = len(all_results)

        # Try local corpus only as a strict offline fallback by default. Mixing
        # generic sample papers into successful external search results can
        # derail downstream gap/principle generation.
        include_local_with_external = _env_bool("FAROS_INCLUDE_LOCAL_WITH_EXTERNAL", False)
        if "local" in sources and self.local and (external_results_count == 0 or include_local_with_external):
            try:
                results = self.local.search(query, limit)
                all_results.extend(results)
                logger.info(f"Local corpus returned {len(results)} results")
            except Exception as e:
                logger.warning(f"Local search failed: {e}")
        
        # Deduplicate by normalized title
        seen_titles = set()
        unique_results = []
        for result in all_results:
            normalized_title = result.title.lower().strip()
            if normalized_title not in seen_titles:
                seen_titles.add(normalized_title)
                unique_results.append(result)
        
        return unique_results[:limit * 2]  # Return up to 2x limit after dedup


# Global service instance
_search_service: Optional[SearchService] = None


def get_search_service() -> SearchService:
    """Get or create search service instance."""
    global _search_service
    if _search_service is None:
        semantic_scholar_key = (
            os.getenv("SEMANTIC_SCHOLAR_API_KEY")
            or os.getenv("S2_API_KEY")
            or None
        )
        local_corpus_path = os.getenv("FAROS_LOCAL_CORPUS_PATH") or None
        _search_service = SearchService(
            semantic_scholar_key=semantic_scholar_key,
            local_corpus_path=local_corpus_path,
        )
    return _search_service
