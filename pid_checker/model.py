"""Neutral internal P&ID model. Every adapter (DEXPI, PDF review, Plant 3D) fills this."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional

SEVERITY_ORDER = ["error", "warning", "review", "info"]

# kinds: equipment, nozzle, valve, fitting, segment, offpage, instrument, loop, container, other
PIPING_KINDS = {"valve", "fitting", "segment"}
PROCESS_KINDS = PIPING_KINDS | {"equipment", "nozzle", "offpage"}


@dataclass
class Obj:
    id: str
    cls: str                      # ComponentClass as in the source ("" if missing)
    kind: str
    tag: Optional[str] = None
    properties: dict = field(default_factory=dict)
    parent_id: Optional[str] = None
    n_nodes: Optional[int] = None  # number of connection nodes, if the source declares it
    source: dict = field(default_factory=dict)  # element, drawing, location, pdf page/coords ...
    review_status: str = "unreviewed"           # unreviewed | extracted | approved


@dataclass
class Rel:
    source_id: Optional[str]
    target_id: Optional[str]
    type: str = "connection"      # connection | signal | owns | loop | association:<x>
    source_node: Optional[str] = None
    target_node: Optional[str] = None
    confidence: float = 1.0
    provenance: str = "dexpi"
    source: dict = field(default_factory=dict)


@dataclass
class Document:
    file: str
    fmt: str                      # dexpi | neutral-json
    version: str = "unknown"
    parser: str = ""
    drawing: str = ""
    objects: dict = field(default_factory=dict)
    rels: list = field(default_factory=list)
    duplicate_ids: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    unsupported: dict = field(default_factory=dict)

    def add_object(self, o: Obj) -> bool:
        if o.id in self.objects:
            self.duplicate_ids.append(o)
            return False
        self.objects[o.id] = o
        return True

    def to_dict(self) -> dict:
        return {
            "file": self.file, "fmt": self.fmt, "version": self.version, "drawing": self.drawing,
            "objects": [asdict(o) for o in self.objects.values()],
            "relationships": [asdict(r) for r in self.rels],
        }


@dataclass
class Finding:
    rule_id: str
    rule_type: str
    severity: str
    title: str
    message: str
    object_ids: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    certainty: str = "confirmed"       # confirmed | probable | insufficient-data
    status: str = "open"               # open | accepted
    decision_needed: str = "Designer review"
    hint: str = ""
    explanation: str = ""              # optional LLM text, always labelled as such in reports
    accepted_reason: str = ""
    reviewer: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
