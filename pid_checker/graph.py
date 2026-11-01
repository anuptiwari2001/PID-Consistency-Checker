"""Build the connectivity graphs from the neutral model."""
from __future__ import annotations
from dataclasses import dataclass
import networkx as nx
from .model import Document, PROCESS_KINDS


def classify_rel(doc: Document, r) -> str:
    if r.type in ("owns", "loop"):
        return r.type
    a, b = doc.objects.get(r.source_id), doc.objects.get(r.target_id)
    if a is None or b is None:
        return "unresolved"
    if "loop" in (a.kind, b.kind):
        return "loop"
    ia, ib = a.kind == "instrument", b.kind == "instrument"
    if r.type == "signal" or (ia and ib):
        return "signal"
    if ia or ib:
        return "instrument_process"
    if r.type.startswith("association"):
        return "association"
    return "process"


@dataclass
class Graphs:
    process: nx.Graph      # piping/equipment/nozzle/off-page connectivity only
    attach: nx.Graph       # instrument process attachments + signal + loop edges
    rel_class: dict        # id(rel) -> class


def build(doc: Document, min_conf: float = 0.75) -> Graphs:
    P, A = nx.Graph(), nx.Graph()
    for o in doc.objects.values():
        if o.kind in PROCESS_KINDS and o.kind != "segment":
            P.add_node(o.id)
        if o.kind == "instrument":
            A.add_node(o.id)
    rc = {}
    for r in doc.rels:
        c = classify_rel(doc, r)
        rc[id(r)] = c
        if r.source_id is None or r.target_id is None or r.confidence < min_conf:
            continue
        if c == "process":
            P.add_edge(r.source_id, r.target_id)
        elif c in ("instrument_process", "signal", "loop"):
            A.add_edge(r.source_id, r.target_id)
    return Graphs(P, A, rc)
