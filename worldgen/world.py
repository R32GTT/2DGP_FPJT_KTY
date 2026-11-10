"""World data containers shared by generation, validation and rendering."""

from __future__ import annotations

from dataclasses import dataclass, field

from .grid import WorldGrid


@dataclass
class RoadNode:
    id: str
    kind: str
    x: float
    y: float
    facility_id: str | None = None


@dataclass
class RoadEdge:
    a: str
    b: str
    points: list[tuple[float, float]] = field(default_factory=list)
    length_m: float = 0.0


@dataclass
class RoadNetwork:
    nodes: dict[str, RoadNode] = field(default_factory=dict)
    edges: list[RoadEdge] = field(default_factory=list)

    def edge_keys(self) -> set[tuple[str, str]]:
        return {tuple(sorted((edge.a, edge.b))) for edge in self.edges}

    def connected(self) -> bool:
        if not self.nodes:
            return True
        adjacency: dict[str, set[str]] = {node_id: set() for node_id in self.nodes}
        for edge in self.edges:
            if edge.a in adjacency and edge.b in adjacency:
                adjacency[edge.a].add(edge.b)
                adjacency[edge.b].add(edge.a)
        start = next(iter(self.nodes))
        seen = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for other in adjacency.get(current, ()):
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        return len(seen) == len(self.nodes)


@dataclass
class Tree:
    id: int
    x: float
    y: float
    trunk_radius_m: float
    canopy_radius_m: float
    trunk_cells: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class FacilityPlacement:
    id: str
    prefab_id: str
    facility_type: str
    x: float
    y: float
    width_m: float
    height_m: float
    rotation: int
    entrance: tuple[float, float]
    outward: tuple[float, float]
    connector_type: str = "pedestrian"
    doors: list[dict] = field(default_factory=list)
    nav_nodes: list[dict] = field(default_factory=list)
    nav_edges: list[dict] = field(default_factory=list)
    occluder_cells: list[tuple[int, int]] = field(default_factory=list)
    blocked_cells: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class ValidationReport:
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reachable_cells: int = 0
    walkable_cells: int = 0
    targets: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "reachable_cells": self.reachable_cells,
            "walkable_cells": self.walkable_cells,
            "reachable_ratio": round(
                self.reachable_cells / max(1, self.walkable_cells), 4
            ),
            "targets": dict(self.targets),
        }


@dataclass
class WorldMap:
    seed: int
    width_m: float
    height_m: float
    grid: WorldGrid
    spawn: tuple[float, float]
    gate: tuple[float, float] | None = None
    facilities: list[FacilityPlacement] = field(default_factory=list)
    trees: list[Tree] = field(default_factory=list)
    roads: RoadNetwork | None = None
    validation: ValidationReport | None = None
    generation_ms: float = 0.0

    def summary(self) -> dict:
        return {
            "format": "revenant_lost.worldgen",
            "generation_version": 1,
            "seed": self.seed,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "grid": {"width": self.grid.width, "height": self.grid.height},
            "spawn": [round(self.spawn[0], 2), round(self.spawn[1], 2)],
            "gate": None if self.gate is None else [round(self.gate[0], 2), round(self.gate[1], 2)],
            "facilities": [
                {
                    "id": placement.id,
                    "prefab": placement.prefab_id,
                    "type": placement.facility_type,
                    "position": [round(placement.x, 2), round(placement.y, 2)],
                    "rotation": placement.rotation,
                    "entrance": [
                        round(placement.entrance[0], 2),
                        round(placement.entrance[1], 2),
                    ],
                }
                for placement in self.facilities
            ],
            "trees": len(self.trees),
            "roads": None
            if self.roads is None
            else {
                "nodes": len(self.roads.nodes),
                "edges": len(self.roads.edges),
                "connected": self.roads.connected(),
            },
            "validation": None
            if self.validation is None
            else self.validation.to_dict(),
            "generation_ms": round(self.generation_ms, 2),
        }
