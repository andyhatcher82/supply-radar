"""Viator's own category taxonomy.

Three tiers, loaded verbatim from the paths they publish. Everything the tool
shows a Destination Specialist is expressed in these terms, because a lead
described as "boat_tour" is a lead described in my vocabulary, and one described
as "Outdoor Activities / On the Water / Sailing" is described in theirs.

The internal category ids are kept as the pipeline's working vocabulary and
mapped onto the taxonomy at the edges. That is deliberate: the ids are stable
keys the classifier and demand table are built on, while the taxonomy is
someone else's data that will change without warning. Swapping the file should
not mean re-running classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from supply_radar.config import CONFIG_DIR

# Internal category id -> the Viator node it belongs under.
#
# Where an internal category spans more than one Viator node, the primary is
# listed first. Sailing sits under both "Outdoor Activities / On the Water" and
# "Tours, Sightseeing & Cruises / Cruises & Sailing" in their own taxonomy, so
# the ambiguity is theirs and is preserved rather than resolved away.
CATEGORY_MAP: dict[str, list[str]] = {
    "boat_tour": [
        "Tours, Sightseeing & Cruises/Cruises & Sailing",
        "Outdoor Activities/On the Water",
    ],
    "water_sports": ["Outdoor Activities/On the Water"],
    "walking_tour": [
        "Tours, Sightseeing & Cruises/How to Get Around/Walking Tours",
        "Tours, Sightseeing & Cruises/Sightseeing Tours/City Tours",
    ],
    "food_drink": ["Food & Drink"],
    "classes_workshops": ["Classes & Workshops"],
    "adventure": [
        "Outdoor Activities/Extreme Sports",
        "Tours, Sightseeing & Cruises/Sightseeing Tours/Adventure Tours",
    ],
    "cultural": ["Art & Culture/Culture"],
    "day_trip": ["Tours, Sightseeing & Cruises/Tours by Duration/Day Trips"],
    "private_guide": ["Tours, Sightseeing & Cruises/Private and Luxury"],
    "transfer": ["Travel & Transportation Services/Transfers"],
    "other": [],
}


@dataclass
class Node:
    path: str
    name: str
    tier: int
    children: list[str] = field(default_factory=list)

    @property
    def parent(self) -> str | None:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else None


@lru_cache
def load_taxonomy() -> dict[str, Node]:
    text = (CONFIG_DIR / "taxonomy" / "viator_categories.txt").read_text(
        encoding="utf-8"
    )
    nodes: dict[str, Node] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("/")
        # Ensure ancestors exist even if a parent line were ever missing.
        for depth in range(1, len(parts) + 1):
            path = "/".join(parts[:depth])
            if path not in nodes:
                nodes[path] = Node(path=path, name=parts[depth - 1], tier=depth)
    for path, node in nodes.items():
        if node.parent and node.parent in nodes:
            nodes[node.parent].children.append(path)
    return nodes


def tier1() -> list[Node]:
    return [n for n in load_taxonomy().values() if n.tier == 1]


def is_valid(path: str) -> bool:
    return path in load_taxonomy()


def label(category_id: str) -> str:
    """Viator's own wording for an internal category, for display."""
    paths = CATEGORY_MAP.get(category_id) or []
    if not paths:
        return category_id.replace("_", " ")
    return load_taxonomy()[paths[0]].name


def full_path(category_id: str) -> str | None:
    paths = CATEGORY_MAP.get(category_id) or []
    return paths[0] if paths else None


def breadcrumb(category_id: str) -> str | None:
    """'Outdoor Activities / On the Water' — what a Destination Specialist
    would recognise from their own filters."""
    path = full_path(category_id)
    return path.replace("/", " / ") if path else None


def top_level(category_id: str) -> str | None:
    path = full_path(category_id)
    return path.split("/")[0] if path else None


def unmapped_categories() -> list[str]:
    """Internal categories with no Viator node. Reported rather than hidden,
    because an unmapped category is a lead the supply team cannot file."""
    return [c for c, paths in CATEGORY_MAP.items() if not paths and c != "other"]


def coverage() -> dict:
    """How much of their taxonomy this build actually searches for.

    Honest framing for the deck: the pipeline currently targets a slice of
    Viator's catalogue, and the slice is measurable rather than vague.
    """
    nodes = load_taxonomy()
    mapped = {p for paths in CATEGORY_MAP.values() for p in paths}
    covered_tops = {p.split("/")[0] for p in mapped}
    return {
        "total_nodes": len(nodes),
        "tier1": len([n for n in nodes.values() if n.tier == 1]),
        "tier2": len([n for n in nodes.values() if n.tier == 2]),
        "tier3": len([n for n in nodes.values() if n.tier == 3]),
        "mapped_nodes": len(mapped),
        "tier1_covered": sorted(covered_tops),
        "tier1_not_covered": sorted(
            n.name for n in nodes.values() if n.tier == 1 and n.name not in covered_tops
        ),
    }
