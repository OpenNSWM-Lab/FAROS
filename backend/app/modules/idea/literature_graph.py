"""
Literature Graph Builder (Graph 1)

Constructs paper-level literature graphs from RawPaper objects.
Handles edge generation, clustering, paper selection, and versioning.

Inspirations:
- Agents4Science cartographer.py: multi-metric scoring, evidence linking
- LECTOR: reasoning logic graph edge types (deduction/abduction/induction)
"""

import logging
import uuid
import hashlib
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple, Set
from math import sqrt

from app.models.idea import (
    LiteratureGraph,
    PaperNode,
    PaperEdge,
    LiteratureCluster,
    RawPaper,
    QueryPlan,
    _compute_title_hash,
)

logger = logging.getLogger(__name__)


class LiteratureGraphBuilder:
    """Stateless builder for paper-level literature graphs."""

    # ------------------------------------------------------------------
    # Edge Generation
    # ------------------------------------------------------------------

    def build_graph_v0(
        self,
        session_id: str,
        raw_papers: List[RawPaper],
        query_plan: Optional[QueryPlan] = None,
    ) -> LiteratureGraph:
        """Build LiteratureGraph v0 from raw papers.

        Args:
            session_id: Parent session ID.
            raw_papers: Deduplicated raw papers from literature search.
            query_plan: Optional QueryPlan for context-aware edge generation.

        Returns:
            LiteratureGraph with version=0 (nodes + edges, no clusters/selection).
        """
        graph_id = f"lg_{uuid.uuid4().hex[:12]}"

        # Create nodes
        nodes = [
            PaperNode(
                paperId=paper.id,
                title=paper.title,
                year=paper.year,
            )
            for paper in raw_papers
        ]

        # Generate edges
        edges: List[PaperEdge] = []
        edges.extend(self._generate_semantic_similar_edges(raw_papers))
        edges.extend(self._generate_citation_edges(raw_papers))
        edges.extend(self._generate_concept_edges(raw_papers))
        edges.extend(self._generate_author_edges(raw_papers))
        edges.extend(self._generate_evidence_edges(raw_papers))

        graph = LiteratureGraph(
            id=graph_id,
            sessionId=session_id,
            version=0,
            nodes=nodes,
            edges=edges,
            clusters=[],
        )

        # Compute per-node metrics (centrality, etc.)
        graph = self._compute_paper_metrics(graph)

        logger.info(
            "Built LiteratureGraph v0: %s with %d nodes, %d edges",
            graph_id, len(nodes), len(edges),
        )
        return graph

    def _generate_semantic_similar_edges(
        self, papers: List[RawPaper]
    ) -> List[PaperEdge]:
        """Generate semantic similarity edges via embedding or Jaccard fallback.

        Primary: litellm embedding with text-embedding-3-small.
        Fallback: title+abstract token Jaccard similarity.
        """
        if len(papers) < 2:
            return []

        edges: List[PaperEdge] = []

        # Try embedding path first
        try:
            import litellm
            texts = [
                f"{p.title}. {p.abstract[:2000]}"
                for p in papers
            ]
            response = litellm.embedding(
                model="text-embedding-3-small",
                input=texts,
            )
            embeddings = [d["embedding"] for d in response.data]
            method = "embedding"
        except Exception as e:
            logger.warning(
                "Embedding failed (%s), falling back to Jaccard", e
            )
            embeddings = None
            method = "jaccard"

        if embeddings is not None:
            # Cosine similarity on embeddings
            for i in range(len(papers)):
                sims = []
                for j in range(len(papers)):
                    if i == j:
                        continue
                    sim = self._cosine_similarity(embeddings[i], embeddings[j])
                    sims.append((j, sim))
                # Top-K per node
                sims.sort(key=lambda x: x[1], reverse=True)
                for j, sim in sims[:10]:
                    if sim >= 0.65:
                        edges.append(PaperEdge(
                            sourceId=papers[i].id,
                            targetId=papers[j].id,
                            edgeType="semantic_similar",
                            weight=round(sim, 3),
                            metadata={"method": method},
                        ))
        else:
            # Jaccard fallback
            token_sets = [
                set(self._tokenize(f"{p.title} {p.abstract}"))
                for p in papers
            ]
            for i in range(len(papers)):
                sims = []
                for j in range(len(papers)):
                    if i == j:
                        continue
                    jaccard = self._jaccard(token_sets[i], token_sets[j])
                    if jaccard >= 0.15:
                        sims.append((j, jaccard))
                sims.sort(key=lambda x: x[1], reverse=True)
                for j, jaccard in sims[:10]:
                    edges.append(PaperEdge(
                        sourceId=papers[i].id,
                        targetId=papers[j].id,
                        edgeType="semantic_similar",
                        weight=round(jaccard, 3),
                        metadata={"method": method},
                    ))

        return edges

    def _generate_citation_edges(
        self, papers: List[RawPaper]
    ) -> List[PaperEdge]:
        """Generate citation edges via co-author + year heuristic.

        Weight tiers:
        - 0.7: shared first-author, year diff <= 3
        - 0.5: shared co-author
        - 0.3: same venue + year proximity
        """
        if len(papers) < 2:
            return []

        edges: List[PaperEdge] = []

        for i in range(len(papers)):
            for j in range(i + 1, len(papers)):
                p_i, p_j = papers[i], papers[j]
                weight = 0.0
                meta: Dict[str, Any] = {"method": "coauthor_heuristic"}

                authors_i = set(a.lower() for a in p_i.authors)
                authors_j = set(a.lower() for a in p_j.authors)
                shared = authors_i & authors_j

                if shared:
                    # Check first-author overlap
                    if (
                        p_i.authors
                        and p_j.authors
                        and p_i.authors[0].lower() == p_j.authors[0].lower()
                    ):
                        year_diff = abs(
                            (p_i.year or 2000) - (p_j.year or 2000)
                        )
                        weight = 0.7 if year_diff <= 3 else 0.5
                        meta["shared_first_author"] = True
                        meta["year_diff"] = year_diff
                    elif len(shared) >= 2:
                        weight = 0.6
                        meta["shared_authors"] = sorted(shared)
                    else:
                        weight = 0.3
                        meta["shared_authors"] = sorted(shared)

                elif p_i.venue and p_j.venue and p_i.venue == p_j.venue:
                    year_diff = abs(
                        (p_i.year or 2000) - (p_j.year or 2000)
                    )
                    if year_diff <= 5:
                        weight = 0.3
                        meta["same_venue"] = True
                        meta["year_diff"] = year_diff

                if weight > 0:
                    edges.append(PaperEdge(
                        sourceId=p_i.id,
                        targetId=p_j.id,
                        edgeType="citation",
                        weight=round(weight, 2),
                        metadata=meta,
                    ))

        return edges

    def _generate_concept_edges(
        self, papers: List[RawPaper]
    ) -> List[PaperEdge]:
        """Generate concept edges via shared keywords from titles and abstracts."""
        if len(papers) < 2:
            return []

        # Extract keyword tokens per paper (top-20 TF)
        paper_keywords: List[Tuple[RawPaper, Set[str]]] = []
        for paper in papers:
            text = f"{paper.title} {paper.abstract}"
            tokens = self._tokenize(text)
            # Simple TF ranking: top-20 frequent tokens
            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            top_tokens = set(
                sorted(tf, key=tf.get, reverse=True)[:20]
            )
            # Also extract bigrams
            bigrams = set()
            for k in range(len(tokens) - 1):
                bigrams.add(f"{tokens[k]}_{tokens[k+1]}")
            paper_keywords.append((paper, top_tokens | bigrams))

        edges: List[PaperEdge] = []
        for i in range(len(paper_keywords)):
            for j in range(i + 1, len(paper_keywords)):
                p_i, kw_i = paper_keywords[i]
                p_j, kw_j = paper_keywords[j]
                shared = kw_i & kw_j
                if len(shared) >= 2:
                    weight = min(1.0, len(shared) / 10.0)
                    edges.append(PaperEdge(
                        sourceId=p_i.id,
                        targetId=p_j.id,
                        edgeType="concept",
                        weight=round(weight, 3),
                        metadata={
                            "shared_terms": sorted(shared)[:10],
                            "shared_count": len(shared),
                        },
                    ))

        return edges

    def _generate_author_edges(
        self, papers: List[RawPaper]
    ) -> List[PaperEdge]:
        """Generate author edges via shared authors."""
        if len(papers) < 2:
            return []

        edges: List[PaperEdge] = []
        for i in range(len(papers)):
            for j in range(i + 1, len(papers)):
                p_i, p_j = papers[i], papers[j]
                authors_i = set(a.lower() for a in p_i.authors)
                authors_j = set(a.lower() for a in p_j.authors)
                shared = authors_i & authors_j
                if shared:
                    weight = 0.6 if len(shared) >= 2 else 0.3
                    edges.append(PaperEdge(
                        sourceId=p_i.id,
                        targetId=p_j.id,
                        edgeType="author",
                        weight=weight,
                        metadata={
                            "shared_authors": sorted(shared),
                            "count": len(shared),
                        },
                    ))

        return edges

    def _generate_evidence_edges(
        self, papers: List[RawPaper]
    ) -> List[PaperEdge]:
        """Generate evidence edges for papers citing same external references.

        Inspired by Agents4Science's scorecard evidence linking pattern.
        Uses shared references from Semantic Scholar metadata when available.
        """
        if len(papers) < 2:
            return []

        # Collect paper IDs cited by each paper (from metadata, if available)
        # For MVP, we use venue co-occurrence as a proxy for shared reference contexts
        edges: List[PaperEdge] = []
        venue_groups: Dict[str, List[RawPaper]] = defaultdict(list)
        for paper in papers:
            if paper.venue:
                venue_groups[paper.venue.lower()].append(paper)

        for venue, group in venue_groups.items():
            if len(group) >= 3:
                # Papers in the same venue that also share concept overlap
                # get evidence edges
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        edges.append(PaperEdge(
                            sourceId=group[i].id,
                            targetId=group[j].id,
                            edgeType="evidence",
                            weight=0.4,
                            metadata={
                                "method": "venue_cooccurrence",
                                "venue": venue,
                            },
                        ))

        return edges

    # ------------------------------------------------------------------
    # Node Metrics (Agents4Science-inspired multi-metric scoring)
    # ------------------------------------------------------------------

    def _compute_paper_metrics(self, graph: LiteratureGraph) -> LiteratureGraph:
        """Compute per-node metrics: degree centrality, betweenness, clustering.

        Returns a new LiteratureGraph with updated node metadata.
        """
        # Build adjacency list (undirected)
        adj: Dict[str, List[str]] = defaultdict(list)
        for edge in graph.edges:
            adj[edge.sourceId].append(edge.targetId)
            adj[edge.targetId].append(edge.sourceId)

        paper_ids = {n.paperId for n in graph.nodes}
        n = len(paper_ids)

        # Degree centrality
        max_degree = max((len(adj[pid]) for pid in paper_ids), default=1)
        degree_centrality = {
            pid: len(adj[pid]) / max(max_degree, 1) for pid in paper_ids
        }

        # Approximate betweenness via BFS (sampled)
        betweenness: Dict[str, float] = {pid: 0.0 for pid in paper_ids}
        sample_nodes = list(paper_ids)[:20]  # Sample up to 20 sources
        for source in sample_nodes:
            # BFS from source
            paths: Dict[str, int] = {source: 1}  # node -> number of shortest paths
            dist: Dict[str, float] = {source: 0.0}
            queue = [source]
            predecessors: Dict[str, List[str]] = defaultdict(list)
            while queue:
                v = queue.pop(0)
                for w in adj[v]:
                    if w not in dist:
                        dist[w] = dist[v] + 1
                        queue.append(w)
                    if dist[w] == dist[v] + 1:
                        paths[w] = paths.get(w, 0) + paths.get(v, 0)
                        predecessors[w].append(v)
            # Back-propagate dependencies (simplified brandes)
            for node in paper_ids:
                if node != source and node in predecessors:
                    for pred in predecessors[node]:
                        if pred != source:
                            betweenness[pred] += 1.0 / max(len(predecessors[node]), 1)

        # Normalize
        max_bet = max(betweenness.values(), default=1.0)
        for pid in betweenness:
            betweenness[pid] = betweenness[pid] / max(max_bet, 1.0)

        # Clustering coefficient
        clustering: Dict[str, float] = {}
        for pid in paper_ids:
            neighbors = adj[pid]
            k = len(neighbors)
            if k < 2:
                clustering[pid] = 0.0
                continue
            # Count edges between neighbors
            neighbor_set = set(neighbors)
            edges_between = 0
            for e in graph.edges:
                if e.sourceId in neighbor_set and e.targetId in neighbor_set:
                    edges_between += 1
            possible = k * (k - 1) / 2.0
            clustering[pid] = edges_between / possible if possible > 0 else 0.0

        # Update node metadata
        updated_nodes = []
        for node in graph.nodes:
            updated_nodes.append(PaperNode(
                paperId=node.paperId,
                title=node.title,
                year=node.year,
                clusterId=node.clusterId,
                role=node.role,
                isSelected=node.isSelected,
                metadata={
                    "degree_centrality": round(degree_centrality.get(node.paperId, 0.0), 3),
                    "betweenness": round(betweenness.get(node.paperId, 0.0), 3),
                    "clustering_coefficient": round(clustering.get(node.paperId, 0.0), 3),
                },
            ))

        return LiteratureGraph(
            id=graph.id,
            sessionId=graph.sessionId,
            version=graph.version,
            nodes=updated_nodes,
            edges=graph.edges,
            clusters=graph.clusters,
            createdAt=graph.createdAt,
        )

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def cluster_papers(self, graph: LiteratureGraph) -> LiteratureGraph:
        """Cluster papers using connected-components on edge adjacency.

        Large components (>20 papers) are subdivided via Jaccard similarity.
        Assigns clusterId to each node and creates LiteratureCluster objects.
        """
        # Build adjacency from edges (undirected, weight >= 0.3)
        adj: Dict[str, Set[str]] = defaultdict(set)
        for edge in graph.edges:
            if edge.weight >= 0.3:
                adj[edge.sourceId].add(edge.targetId)
                adj[edge.targetId].add(edge.sourceId)

        # Connected components via BFS
        visited: Set[str] = set()
        components: List[Set[str]] = []
        for node in graph.nodes:
            if node.paperId in visited:
                continue
            comp: Set[str] = set()
            queue = [node.paperId]
            while queue:
                v = queue.pop(0)
                if v in visited:
                    continue
                visited.add(v)
                comp.add(v)
                for neighbor in adj.get(v, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            components.append(comp)

        # Subdivide large components via Jaccard
        paper_texts: Dict[str, str] = {}
        for node in graph.nodes:
            paper_texts[node.paperId] = node.title  # Title-only Jaccard for speed

        final_clusters: List[Set[str]] = []
        for comp in components:
            if len(comp) <= 20:
                final_clusters.append(comp)
            else:
                # Subdivide: simple K-means-like split using Jaccard
                subdivided = self._subdivide_component(comp, paper_texts)
                final_clusters.extend(subdivided)

        # Assign clusters
        updated_nodes: List[PaperNode] = []
        clusters: List[LiteratureCluster] = []

        for idx, comp in enumerate(final_clusters):
            cluster_id = f"cl_{uuid.uuid4().hex[:8]}"
            comp_list = sorted(comp)

            # Compute centroid: paper with highest mean similarity to others
            centroid_id = self._find_centroid(comp_list, paper_texts)

            # Extract theme tokens: top-10 TF-IDF tokens across cluster papers
            theme_tokens = self._extract_theme_tokens(
                comp_list, paper_texts, top_k=10
            )

            clusters.append(LiteratureCluster(
                clusterId=cluster_id,
                label=f"Cluster {idx + 1}",
                paperIds=comp_list,
                centroidPaperId=centroid_id,
                themeTokens=theme_tokens,
            ))

            # Update nodes in this cluster
            for node in graph.nodes:
                if node.paperId in comp:
                    updated_nodes.append(PaperNode(
                        paperId=node.paperId,
                        title=node.title,
                        year=node.year,
                        clusterId=cluster_id,
                        role=node.role,
                        isSelected=node.isSelected,
                        metadata=node.metadata,
                    ))

        # Keep nodes not in any cluster
        clustered_ids = {n.paperId for n in updated_nodes}
        for node in graph.nodes:
            if node.paperId not in clustered_ids:
                updated_nodes.append(node)

        return LiteratureGraph(
            id=graph.id,
            sessionId=graph.sessionId,
            version=graph.version,
            nodes=updated_nodes,
            edges=graph.edges,
            clusters=clusters,
            createdAt=graph.createdAt,
        )

    def _subdivide_component(
        self, comp: Set[str], paper_texts: Dict[str, str]
    ) -> List[Set[str]]:
        """Subdivide a large component via Jaccard similarity clustering."""
        comp_list = list(comp)
        # Compute pairwise Jaccard
        token_sets = {
            pid: set(self._tokenize(paper_texts.get(pid, "")))
            for pid in comp_list
        }
        # Simple agglomerative: start with each paper as its own cluster,
        # merge if Jaccard > threshold
        threshold = 0.10
        clusters: List[Set[str]] = [{pid} for pid in comp_list]

        merged = True
        while merged and len(clusters) > 1:
            merged = False
            best_pair = None
            best_sim = 0.0
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    sim = self._cluster_jaccard(
                        clusters[i], clusters[j], token_sets
                    )
                    if sim > best_sim:
                        best_sim = sim
                        best_pair = (i, j)
            if best_pair and best_sim > threshold:
                i, j = best_pair
                clusters[i] = clusters[i] | clusters[j]
                clusters.pop(j)
                merged = True

        return clusters

    def _cluster_jaccard(
        self,
        c1: Set[str],
        c2: Set[str],
        token_sets: Dict[str, Set[str]],
    ) -> float:
        """Mean Jaccard similarity between two clusters."""
        # Simplified: union of all tokens
        tokens1: Set[str] = set()
        for pid in c1:
            tokens1 |= token_sets.get(pid, set())
        tokens2: Set[str] = set()
        for pid in c2:
            tokens2 |= token_sets.get(pid, set())
        return self._jaccard(tokens1, tokens2) if tokens1 and tokens2 else 0.0

    def _find_centroid(
        self, paper_ids: List[str], paper_texts: Dict[str, str]
    ) -> Optional[str]:
        """Find centroid paper: highest mean similarity to cluster members."""
        if not paper_ids:
            return None
        if len(paper_ids) == 1:
            return paper_ids[0]

        token_sets = {
            pid: set(self._tokenize(paper_texts.get(pid, "")))
            for pid in paper_ids
        }
        best_pid = paper_ids[0]
        best_mean = -1.0
        for pid in paper_ids:
            sims = []
            for other in paper_ids:
                if pid == other:
                    continue
                sims.append(self._jaccard(
                    token_sets.get(pid, set()),
                    token_sets.get(other, set()),
                ))
            mean_sim = sum(sims) / len(sims) if sims else 0.0
            if mean_sim > best_mean:
                best_mean = mean_sim
                best_pid = pid
        return best_pid

    def _extract_theme_tokens(
        self,
        paper_ids: List[str],
        paper_texts: Dict[str, str],
        top_k: int = 10,
    ) -> List[str]:
        """Extract top TF-IDF tokens as theme descriptors for a cluster."""
        # TF across cluster papers
        tf: Dict[str, int] = {}
        doc_count = 0
        for pid in paper_ids:
            text = paper_texts.get(pid, "")
            if not text:
                continue
            doc_count += 1
            tokens = set(self._tokenize(text))
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1

        # Simple TF-IDF (IDF approximated as 1/tf)
        scored = sorted(
            tf.items(),
            key=lambda x: x[1] * (1.0 / max(x[1], 1)),  # crude IDF
            reverse=True,
        )
        return [t for t, _ in scored[:top_k]]

    # ------------------------------------------------------------------
    # Paper Selection (Agents4Science-inspired multi-role distribution)
    # ------------------------------------------------------------------

    def select_papers(
        self,
        graph: LiteratureGraph,
        num_select: int = 15,
        must_cite_list: Optional[List[str]] = None,
    ) -> Tuple[LiteratureGraph, List[str]]:
        """Select papers by role distribution.

        Role distribution:
        - Core (30%): highest degree centrality per cluster
        - Representative (20%): closest to centroid per cluster
        - Frontier (20%): most recent, highest betweenness
        - Bridge (15%): highest inter-cluster edge count
        - Contradiction (10%): high title sim + low concept overlap
        - Must-cite (5%): matching DOIs/titles from must-cite list

        Args:
            graph: LiteratureGraph v0 with clusters assigned.
            num_select: Max number of papers to select.
            must_cite_list: Optional list of must-cite paper DOIs or title substrings.

        Returns:
            Updated graph with roles and isSelected set, and list of selected IDs.
        """
        if not graph.nodes:
            return graph, []

        # Build inter-cluster edge count map
        cluster_adj: Dict[str, Set[str]] = defaultdict(set)
        paper_to_cluster = {
            n.paperId: n.clusterId for n in graph.nodes if n.clusterId
        }
        inter_cluster_count: Dict[str, int] = defaultdict(int)
        for edge in graph.edges:
            c_src = paper_to_cluster.get(edge.sourceId)
            c_tgt = paper_to_cluster.get(edge.targetId)
            if c_src and c_tgt and c_src != c_tgt:
                inter_cluster_count[edge.sourceId] += 1
                inter_cluster_count[edge.targetId] += 1

        # Group nodes by cluster
        clusters_map: Dict[str, List[PaperNode]] = defaultdict(list)
        for node in graph.nodes:
            cid = node.clusterId or "__unclustered__"
            clusters_map[cid].append(node)

        # Compute per-node scores for each role
        selected: Dict[str, str] = {}  # paperId -> role
        allocations = {
            "must_cite": max(1, int(num_select * 0.05)) if must_cite_list else 0,
            "core": max(1, int(num_select * 0.30)),
            "representative": max(1, int(num_select * 0.20)),
            "frontier": max(1, int(num_select * 0.20)),
            "bridge": max(1, int(num_select * 0.15)),
            "contradiction": max(1, int(num_select * 0.10)),
        }

        # --- Must-cite: from config (MUST run BEFORE other selections) ---
        if must_cite_list and allocations["must_cite"] > 0:
            for node in graph.nodes:
                title_lower = node.title.lower()
                for mc in must_cite_list:
                    if mc.lower() in title_lower:
                        selected[node.paperId] = "must_cite"
                        break
                if len([s for s in selected.values() if s == "must_cite"]) >= allocations["must_cite"]:
                    break

        # --- Core: highest degree centrality per cluster ---
        core_candidates = []
        for cid, nodes in clusters_map.items():
            nodes_sorted = sorted(
                nodes,
                key=lambda n: n.metadata.get("degree_centrality", 0),
                reverse=True,
            )
            core_candidates.extend(nodes_sorted[:2])  # Up to 2 per cluster
        for node in core_candidates[:allocations["core"]]:
            if node.paperId not in selected:
                selected[node.paperId] = "core"

        # --- Representative: closest to centroid ---
        centroid_ids = {c.centroidPaperId for c in graph.clusters if c.centroidPaperId}
        for node in graph.nodes:
            if (
                node.paperId in centroid_ids
                and node.paperId not in selected
                and len([s for s in selected.values() if s == "representative"])
                < allocations["representative"]
            ):
                selected[node.paperId] = "representative"

        # --- Frontier: most recent, highest betweenness ---
        frontier_candidates = sorted(
            graph.nodes,
            key=lambda n: (
                -(n.year or 2000),
                -n.metadata.get("betweenness", 0),
            ),
        )
        for node in frontier_candidates:
            if node.paperId not in selected and len(
                [s for s in selected.values() if s == "frontier"]
            ) < allocations["frontier"]:
                selected[node.paperId] = "frontier"

        # --- Bridge: highest inter-cluster edge count ---
        bridge_candidates = sorted(
            graph.nodes,
            key=lambda n: inter_cluster_count.get(n.paperId, 0),
            reverse=True,
        )
        for node in bridge_candidates:
            if node.paperId not in selected and len(
                [s for s in selected.values() if s == "bridge"]
            ) < allocations["bridge"]:
                selected[node.paperId] = "bridge"

        # --- Contradiction: high title sim + low concept overlap ---
        contradiction_pairs = self._find_contradictions(graph)
        for pid in contradiction_pairs:
            if pid not in selected and len(
                [s for s in selected.values() if s == "contradiction"]
            ) < allocations["contradiction"]:
                selected[pid] = "contradiction"

        # --- Fill remaining slots ---
        remaining = num_select - len(selected)
        if remaining > 0:
            for node in graph.nodes:
                if node.paperId not in selected:
                    selected[node.paperId] = "core"
                    remaining -= 1
                    if remaining <= 0:
                        break

        # Update nodes
        updated_nodes = []
        for node in graph.nodes:
            role = selected.get(node.paperId)
            updated_nodes.append(PaperNode(
                paperId=node.paperId,
                title=node.title,
                year=node.year,
                clusterId=node.clusterId,
                role=role,
                isSelected=node.paperId in selected,
                metadata=node.metadata,
            ))

        selected_ids = list(selected.keys())

        logger.info(
            "Selected %d papers: %s",
            len(selected_ids),
            {
                role: len([s for s in selected.values() if s == role])
                for role in set(selected.values())
            },
        )

        return (
            LiteratureGraph(
                id=graph.id,
                sessionId=graph.sessionId,
                version=graph.version,
                nodes=updated_nodes,
                edges=graph.edges,
                clusters=graph.clusters,
                createdAt=graph.createdAt,
            ),
            selected_ids,
        )

    def _find_contradictions(self, graph: LiteratureGraph) -> List[str]:
        """Find papers with potentially contradictory findings.

        Heuristic: papers with high title Jaccard but low concept edge weight
        between them (i.e., similar topics but different conclusions).
        """
        contradictions: Set[str] = set()
        concept_edges: Dict[Tuple[str, str], float] = {}
        for edge in graph.edges:
            if edge.edgeType == "concept":
                key = tuple(sorted([edge.sourceId, edge.targetId]))
                concept_edges[key] = max(
                    concept_edges.get(key, 0), edge.weight
                )

        # Check pairs of nodes in the same cluster with low concept overlap
        cluster_map: Dict[str, List[PaperNode]] = defaultdict(list)
        for node in graph.nodes:
            if node.clusterId:
                cluster_map[node.clusterId].append(node)

        for cid, nodes in cluster_map.items():
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    n_i, n_j = nodes[i], nodes[j]
                    key = tuple(sorted([n_i.paperId, n_j.paperId]))
                    concept_weight = concept_edges.get(key, 0.0)
                    # High title similarity (approximate via shared tokens)
                    tokens_i = set(self._tokenize(n_i.title))
                    tokens_j = set(self._tokenize(n_j.title))
                    title_jaccard = self._jaccard(tokens_i, tokens_j)
                    if title_jaccard > 0.4 and concept_weight < 0.3:
                        contradictions.add(n_i.paperId)
                        contradictions.add(n_j.paperId)

        return list(contradictions)

    # ------------------------------------------------------------------
    # Version Management
    # ------------------------------------------------------------------

    def upgrade_to_v1(self, graph: LiteratureGraph) -> LiteratureGraph:
        """Upgrade a graph to version 1 (clusters and roles frozen)."""
        return LiteratureGraph(
            id=graph.id,
            sessionId=graph.sessionId,
            version=1,
            nodes=graph.nodes,
            edges=graph.edges,
            clusters=graph.clusters,
            createdAt=graph.createdAt,
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Tokenize text into lowercased, stopword-filtered tokens.

        Only removes high-frequency function words, preserving domain terms.
        """
        STOPWORDS = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "can", "shall",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "above",
            "below", "between", "and", "or", "not", "no", "but", "if",
            "while", "although", "this", "that", "these", "those", "it",
            "its", "we", "they", "them", "their", "our", "he", "she",
            "which", "who", "whom", "what", "how", "where", "when",
            "also", "than", "then", "just", "about", "such", "only",
            "other", "some", "any", "each", "all", "both",
            "few", "most", "very", "much", "many", "one", "two",
            "et", "al",
        }
        import re
        words = re.findall(r'[a-z0-9]+', text.lower())
        return [w for w in words if w not in STOPWORDS and len(w) >= 2]

    @staticmethod
    def _jaccard(a: Set[str], b: Set[str]) -> float:
        """Compute Jaccard similarity."""
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sqrt(sum(x * x for x in a))
        norm_b = sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
