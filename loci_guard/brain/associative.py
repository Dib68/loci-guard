"""Associative recall (pattern completion), after HippoRAG's hippocampal indexing.

Memories, rooms and the concepts their texts mention form one graph. A query lights up the
concepts and rooms it names; Personalized PageRank spreads that activation along the graph,
so a partial cue also reaches memories that share a room, a corridor or a concept with what
matched directly. Pure Python and deterministic: no embeddings, no model calls.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from loci_guard.brain.palace import ATRIUM, MemoryPalace
from loci_guard.brain.text import tokens

DAMPING = 0.85
ITERATIONS = 40
ROOM_EDGE = 1.0
PARENT_EDGE = 0.5
CORRIDOR_EDGE = 1.0


class AssociativeIndex:
    def __init__(self, palace: MemoryPalace):
        self.palace = palace
        self.edges: dict[str, dict[str, float]] = defaultdict(dict)
        self.memory_terms: dict[str, set[str]] = {}
        self._build()

    def _link(self, a: str, b: str, weight: float) -> None:
        self.edges[a][b] = self.edges[a].get(b, 0.0) + weight
        self.edges[b][a] = self.edges[b].get(a, 0.0) + weight

    def _build(self) -> None:
        memories = [
            m for m in self.palace.memories.values()
            if m["kind"] != "candidate" and not (m["kind"] == "intention" and m.get("status") != "open")
        ]
        document_frequency: dict[str, int] = defaultdict(int)
        for m in memories:
            terms = tokens(m["text"] + " " + " ".join(m["anchors"]))
            self.memory_terms[m["id"]] = terms
            for t in terms:
                document_frequency[t] += 1
        total = max(len(memories), 1)
        room_pairs: set[tuple[str, str]] = set()
        for m in memories:
            node = f"m:{m['id']}"
            for t in self.memory_terms[m["id"]]:
                # Rare concepts bind more tightly than ones every memory mentions.
                self._link(node, f"t:{t}", math.log(1 + total / document_frequency[t]))
            for anchor in m["anchors"]:
                room = self.palace.room_of(anchor)
                self._link(node, f"r:{room}", ROOM_EDGE)
                chain = self.palace.ancestors(room)
                room_pairs.update((c, p) for c, p in zip(chain, chain[1:]) if p != ATRIUM)
            for linked in m["links"]:
                if linked in self.palace.memories:
                    self._link(node, f"m:{linked}", CORRIDOR_EDGE / 2)  # each side adds half
        for child, parent in room_pairs:
            self._link(f"r:{child}", f"r:{parent}", PARENT_EDGE)

    def _seeds(self, query: str) -> dict[str, float]:
        seeds: dict[str, float] = {}
        for t in tokens(query):
            if f"t:{t}" in self.edges:
                seeds[f"t:{t}"] = 1.0
        for part in query.split():
            anchor = self.palace.anchor_for(part.rstrip("/\\") + "/") if "/" in part or "\\" in part else None
            if anchor and f"r:{anchor}" in self.edges:
                seeds[f"r:{anchor}"] = 1.0
        return seeds

    def activate(self, query: str) -> dict[str, float]:
        """Personalized PageRank from the query's concepts and rooms."""
        seeds = self._seeds(query)
        if not seeds:
            return {}
        total = sum(seeds.values())
        restart = {n: w / total for n, w in seeds.items()}
        rank = dict(restart)
        strength = {n: sum(nbrs.values()) for n, nbrs in self.edges.items()}
        for _ in range(ITERATIONS):
            nxt: dict[str, float] = defaultdict(float)
            for node, value in rank.items():
                out = strength.get(node, 0.0)
                if not out:
                    continue
                for neighbour, weight in self.edges[node].items():
                    nxt[neighbour] += DAMPING * value * weight / out
            for node, value in restart.items():
                nxt[node] += (1 - DAMPING) * value
            rank = dict(nxt)
        return rank

    def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Memories ranked by activation; each says whether it matched directly or by association."""
        rank = self.activate(query)
        query_terms = tokens(query)
        scored = sorted(
            ((value, node[2:]) for node, value in rank.items() if node.startswith("m:")),
            reverse=True,
        )
        results = []
        for value, memory_id in scored[:limit]:
            memory = self.palace.memories[memory_id]
            shared = sorted(self.memory_terms.get(memory_id, set()) & query_terms)
            results.append({
                **memory,
                "activation": value,
                "match": ("matched: " + ", ".join(shared)) if shared else "by association",
            })
        return results
