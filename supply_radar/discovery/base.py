"""Discovery source contract.

Adding a source, whether a tourist board directory, a licensing register or
another API, means implementing this and adding a line to a destination pack.
It should never mean touching the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from supply_radar.geometry import SearchArea
from supply_radar.models import DiscoveredPlace


@dataclass
class SweepResult:
    places: list[DiscoveredPlace] = field(default_factory=list)
    cells_queried: int = 0
    cells_subdivided: int = 0
    truncated_cells: int = 0
    api_calls: int = 0
    errors: list[str] = field(default_factory=list)

    # Cells that were still returning a full page at maximum subdivision depth.
    # These are the only places the sweep KNOWS it is incomplete, and they are
    # reported rather than swallowed.
    unresolved_cells: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "places": len(self.places),
            "cells_queried": self.cells_queried,
            "cells_subdivided": self.cells_subdivided,
            "truncated_cells": self.truncated_cells,
            "api_calls": self.api_calls,
            "unresolved_cells": len(self.unresolved_cells),
            "errors": len(self.errors),
        }


class DiscoverySource(Protocol):
    name: str

    def sweep(self, area: SearchArea, **kwargs) -> SweepResult:
        ...
