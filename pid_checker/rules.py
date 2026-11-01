"""Deterministic rule engine. Rule types are kept separate on purpose (see RULES)."""
from __future__ import annotations
import re
from collections import defaultdict
import networkx as nx
from .model import Document, Finding, SEVERITY_ORDER
from .graph import build, classify_rel

# rule_id: (rule type, default severity, title)
RULES = {
    "DEXPI-ID-001":   ("DEXPI/model validation", "error",   "Duplicate object ID"),
    "DEXPI-REF-001":  ("DEXPI/model validation", "error",   "Connection references unknown object"),
    "DEXPI-NODE-001": ("DEXPI/model validation", "error",   "Connection node does not belong to the referenced object"),
    "CONN-001":       ("Connectivity rule",      "warning", "Unconnected process nozzle"),
    "CONN-002":       ("Connectivity rule",      "error",   "Incomplete piping connection"),
    "CONN-003":       ("Connectivity rule",      "error",   "Line ends with no valid target"),
    "CONN-004":       ("Connectivity rule",      "review",  "Orphaned instrument"),
    "CONN-005":       ("Connectivity rule",      "warning", "Isolated piping network"),
    "PRJ-TAG-001":    ("Project rule",           "error",   "Duplicate tag"),
    "PRJ-REQ-001":    ("Project rule",           "warning", "Required property missing"),
    "CLT-TAG-001":    ("Client standard",        "warning", "Tag does not match client pattern"),
}

DEFAULT_CFG = {
    "rules": {},
    "duplicate_tag": {"scope": "project"},          # project | drawing | unit
    "terminal_class_keywords": ["blind", "cap", "plug", "vent", "drain", "spectacle"],
    "nozzle_status_properties": ["NozzleStatus", "Status", "NozzleType"],
    "nozzle_note_properties": ["Remarks", "Remark", "Notes", "Note", "Comment", "Comments", "Description"],
    "nozzle_class_exclude": [],
    "required": {},
    "client_tag_patterns": {},
}


def merge_cfg(user: dict | None) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
           for k, v in DEFAULT_CFG.items()}
    for k, v in (user or {}).items():
        cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) and isinstance(cfg.get(k), dict) else v
    return cfg


def _ev(o) -> dict:
    return {"id": o.id, "tag": o.tag, "class": o.cls or o.source.get("element"), "kind": o.kind,
            "drawing": o.source.get("drawing"), "location": o.source.get("location"),
            "review_status": o.review_status}


def _label(doc, o) -> str:
    parent = doc.objects.get(o.parent_id) if o.parent_id else None
    t = o.tag or o.id
    return f"{parent.tag or parent.id}/{t}" if (parent and o.kind == "nozzle") else t


def _certainty(doc, objs) -> str:
    if doc.fmt == "dexpi":
        return "confirmed"
    return "confirmed" if all(o.review_status == "approved" for o in objs) else "probable"


def _get_prop(o, names) -> str:
    low = {k.lower(): v for k, v in o.properties.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, ""):
            return v
    return ""


def _is_terminal(o, cfg) -> bool:
    c = (o.cls or "").lower()
    return any(k in c for k in cfg["terminal_class_keywords"])


class Engine:
    def __init__(self, doc: Document, cfg: dict | None = None, extractor=None):
        self.doc, self.cfg, self.ex = doc, merge_cfg(cfg), extractor
        self.G = build(doc)
        self.findings: list[Finding] = []

    def _rule_cfg(self, rid):
        return self.cfg["rules"].get(rid, {})

    def add(self, rid, message, objs, severity=None, certainty=None, hint="", title=None, decision="Designer review"):
        rc = self._rule_cfg(rid)
        if rc.get("enabled") is False:
            return None
        rtype, sev, ttl = RULES[rid]
        f = Finding(rule_id=rid, rule_type=rtype, severity=severity or rc.get("severity") or sev,
                    title=title or ttl, message=message, object_ids=[o.id for o in objs],
                    tags=[o.tag or o.id for o in objs], evidence=[_ev(o) for o in objs[:10]],
                    certainty=certainty or _certainty(self.doc, objs), hint=hint, decision_needed=decision)
        self.findings.append(f)
        return f

    # ---------- A. DEXPI / model validation ----------
    def model_rules(self):
        d = self.doc
        for o in d.duplicate_ids:
            self.add("DEXPI-ID-001", f"ID '{o.id}' is used by more than one object "
                     f"(second one: {o.cls or o.source.get('element')} {o.tag or ''}).", [o], certainty="confirmed")
        for r in d.rels:
            a, b = d.objects.get(r.source_id), d.objects.get(r.target_id)
            for rid_, obj, name in ((r.source_id, a, "source"), (r.target_id, b, "target")):
                if rid_ and obj is None:
                    ghost = type("Ghost", (), dict(id=rid_, tag=None, cls="", kind="unknown",
                                                   source={}, review_status="n/a"))()
                    self.add("DEXPI-REF-001", f"Connection {name} '{rid_}' does not exist in the model.",
                             [ghost], certainty="confirmed")
            for obj, node, name in ((a, r.source_node, "source"), (b, r.target_node, "target")):
                if obj is not None and node not in (None, ""):
                    try:
                        idx = int(node)
                    except ValueError:
                        continue
                    if obj.n_nodes is not None and not (1 <= idx <= obj.n_nodes):
                        self.add("DEXPI-NODE-001", f"Connection uses node {idx} of {obj.tag or obj.id}, "
                                 f"which declares only {obj.n_nodes} node(s).", [obj], certainty="confirmed")

    # ---------- B. connectivity ----------
    def _nozzle_exception(self, n):
        """Return (kind, status, reason) where kind is 'source' (declared in data), 'note' or None."""
        v = _get_prop(n, self.cfg["nozzle_status_properties"])
        if v:
            from .llm import heuristic_status
            h = heuristic_status(v)
            if h["status"] != "unknown":
                return "source", h["status"], f"source property = '{v}'"
        note = " ; ".join(x for x in (_get_prop(n, [p]) for p in self.cfg["nozzle_note_properties"]) if x)
        if note and self.ex is not None:
            r = self.ex.classify(note)
            if r["status"] not in ("unknown", "active"):
                return "note", r["status"], f"note '{r['evidence']}' ({r['source']}, {r['confidence']})"
        return None, None, ""

    def connectivity_rules(self):
        d, P, A = self.doc, self.G.process, self.G.attach
        # CONN-001 nozzles
        for n in (o for o in d.objects.values() if o.kind == "nozzle"):
            if any(k.lower() in (n.cls or "").lower() for k in self.cfg["nozzle_class_exclude"]):
                continue
            if P.degree(n.id) == 0:
                kind, status, why = self._nozzle_exception(n)
                lbl = _label(d, n)
                if kind == "source":
                    f = self.add("CONN-001", f"Process nozzle {lbl} has no connected piping; declared {status} ({why}).",
                                 [n], severity="info")
                    if f:
                        f.status, f.accepted_reason, f.reviewer = "accepted", f"Declared in source: {status} ({why})", "source data"
                elif kind == "note":
                    self.add("CONN-001", f"Process nozzle {lbl} has no connected piping. A note suggests it is {status}.",
                             [n], severity="review",
                             hint=f"Suggested exception from {why}. Confirm and add to exceptions file.")
                else:
                    self.add("CONN-001", f"Process nozzle {lbl} has no connected piping path.", [n])
        # CONN-002 incomplete connection
        for r in d.rels:
            if r.type != "connection":
                continue
            if (r.source_id is None) != (r.target_id is None):
                present = d.objects.get(r.source_id or r.target_id)
                if present is not None and present.kind == "offpage":
                    continue
                if present is not None:
                    self.add("CONN-002", f"Connection from/to {present.tag or present.id} is missing its "
                             f"{'target' if r.target_id is None else 'source'} item.", [present])
        # CONN-003 dead ends (islands are reported once, by CONN-005)
        island_ids = set()
        for comp in nx.connected_components(P):
            if all(d.objects[i].kind in ("valve", "fitting", "segment") for i in comp if i in d.objects):
                island_ids |= comp
        for o in d.objects.values():
            if o.id in island_ids:
                continue
            if o.kind in ("valve", "fitting", "segment") and o.id in P and P.degree(o.id) == 1 and not _is_terminal(o, self.cfg):
                self.add("CONN-003", f"{o.tag or o.id} ({o.cls or o.kind}) is connected on one side only; the line ends "
                         "with no valid target item, off-page connector or terminal.", [o])
        # CONN-004 orphaned instruments
        for o in d.objects.values():
            if o.kind == "instrument" and A.degree(o.id) == 0:
                self.add("CONN-004", f"Instrument {o.tag or o.id} has no identifiable process, signal or loop relationship.", [o])
        # CONN-005 isolated piping islands
        for comp in nx.connected_components(P):
            objs = [d.objects[i] for i in comp if i in d.objects]
            if objs and all(o.kind in ("valve", "fitting", "segment") for o in objs) \
                    and not any(_is_terminal(o, self.cfg) for o in objs):
                self.add("CONN-005", f"Isolated piping network of {len(objs)} item(s) with no equipment nozzle, "
                         f"off-page connector or terminal: {', '.join(o.tag or o.id for o in objs[:6])}.", objs)

    # ---------- C. project / client rules ----------
    def project_rules(self):
        d = self.doc
        scope = self.cfg["duplicate_tag"].get("scope", "project")
        groups = defaultdict(list)
        for o in d.objects.values():
            if o.kind in ("equipment", "valve", "fitting", "instrument") and o.tag:
                if scope == "drawing":
                    key = (o.source.get("drawing") or "", o.tag)
                elif scope == "unit":
                    key = (_get_prop(o, ["Unit", "PlantArea", "Area"]), o.tag)
                else:
                    key = ("", o.tag)
                groups[key].append(o)
        for (sc, tag), objs in groups.items():
            if len(objs) > 1:
                self.add("PRJ-TAG-001", f"Tag '{tag}' is used by {len(objs)} objects (scope: {scope}"
                         f"{': ' + sc if sc else ''}).", objs, decision="Designer / project rule owner")
        req = self.cfg["required"]
        kmap = {"equipment": "equipment", "valve": "valve", "segment": "line", "instrument": "instrument"}
        for o in d.objects.values():
            reqs = req.get(kmap.get(o.kind, ""), [])
            missing = []
            for r in reqs:
                if r == "tag":
                    ok = bool(o.tag)
                elif r == "class":
                    ok = bool(o.cls)
                else:
                    ok = bool(_get_prop(o, r.split("|")))
                if not ok:
                    missing.append(r)
            if missing:
                self.add("PRJ-REQ-001", f"{o.kind.capitalize()} {o.tag or o.id} is missing: {', '.join(missing)}.", [o],
                         decision="Designer review (project completeness rule)")
        for kind, pat in self.cfg["client_tag_patterns"].items():
            rx = re.compile(pat)
            for o in d.objects.values():
                if o.kind == kind and o.tag and not rx.match(o.tag):
                    self.add("CLT-TAG-001", f"Tag '{o.tag}' does not match client pattern {pat}.", [o],
                             decision="Designer review (client naming standard)")

    def run(self) -> list[Finding]:
        self.model_rules()
        self.connectivity_rules()
        self.project_rules()
        self.findings.sort(key=lambda f: (SEVERITY_ORDER.index(f.severity) if f.severity in SEVERITY_ORDER else 9, f.rule_id))
        return self.findings


def apply_exceptions(doc: Document, findings: list[Finding], exceptions: list[dict]) -> None:
    """Accepted exceptions come from a reviewer-maintained file. Matching: rule_id + object id / tag / 'ParentTag/Tag'."""
    def keys(oid):
        o = doc.objects.get(oid)
        if o is None:
            return {oid}
        ks = {o.id}
        if o.tag:
            ks.add(o.tag)
            p = doc.objects.get(o.parent_id) if o.parent_id else None
            if p is not None and p.tag:
                ks.add(f"{p.tag}/{o.tag}")
        return ks
    for f in findings:
        if f.status == "accepted":
            continue
        fk = set().union(*(keys(i) for i in f.object_ids)) if f.object_ids else set()
        for ex in exceptions or []:
            if ex.get("rule_id") in (f.rule_id, "*") and str(ex.get("object")) in fk:
                f.status = "accepted"
                f.accepted_reason = ex.get("reason", "")
                f.reviewer = f"{ex.get('reviewer', '?')} {ex.get('date', '')}".strip()
                break


def inventory(doc: Document, G) -> dict:
    kinds, classes = defaultdict(int), defaultdict(int)
    for o in doc.objects.values():
        kinds[o.kind] += 1
        classes[o.cls or o.source.get("element", "?")] += 1
    P = G.process
    comps = list(nx.connected_components(P))
    rel_counts = defaultdict(int)
    for r in doc.rels:
        rel_counts[G.rel_class.get(id(r), "?")] += 1
    return {"kinds": dict(kinds), "classes": dict(classes), "relationships": dict(rel_counts),
            "process_components": len(comps), "process_nodes": P.number_of_nodes(),
            "process_edges": P.number_of_edges(), "unsupported": dict(doc.unsupported)}
