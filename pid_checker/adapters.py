"""Input adapters: DEXPI 1.3 Proteus XML (lightweight parser) and neutral JSON."""
from __future__ import annotations
import json
import os
import xml.etree.ElementTree as ET
from .model import Document, Obj, Rel

SKIP_TAGS = {"GenericAttributes", "GenericAttribute", "Position", "Location", "Axis", "Reference",
             "Extent", "Min", "Max", "Presentation", "Label", "Text", "ConnectionPoints", "Node",
             "PlantInformation", "Drawing", "UnitsOfMeasure", "Coordinate", "Scale", "Shape",
             "Circle", "Line", "PolyLine", "Ellipse", "Trace", "CenterLine", "Color", "Font"}
INSTR_TAGS = {"ProcessInstrumentationFunction", "ProcessSignalGeneratingFunction", "ActuatingFunction",
              "SignalConveyingFunction", "InstrumentationFunction", "Instrument",
              "SensingLocation", "ActuatingSystem", "ControlFunction"}


def _local(t: str) -> str:
    return t.split("}", 1)[-1]


def _classify(tagname: str, cls: str) -> str:
    c = cls.lower()
    if tagname == "Nozzle" or c.endswith("nozzle"):
        return "nozzle"
    if tagname == "PipingNetworkSegment":
        return "segment"
    if tagname == "PipingNetworkSystem":
        return "container"
    if tagname == "PipeConnectorSymbol" or "offpage" in c or "off-page" in c or "connector" in c:
        return "offpage"
    if tagname == "InstrumentationLoopFunction" or "loop" in c:
        return "loop"
    if tagname in INSTR_TAGS or "instrument" in c:
        return "instrument"
    if tagname == "Equipment":
        return "equipment"
    if tagname in ("PipingComponent", "Valve", "Pipe", "Fitting"):
        return "valve" if "valve" in c or tagname == "Valve" else "fitting"
    return "other"


def parse_proteus(path: str) -> Document:
    """Parse DEXPI 1.3 Proteus XML. DEXPI 2.0.x uses a different serialisation: not supported here."""
    root = ET.parse(path).getroot()
    doc = Document(file=os.path.basename(path), fmt="dexpi", parser="pid_checker proteus-lite 0.1")
    for el in root.iter():
        n = _local(el.tag)
        if n == "PlantInformation":
            doc.version = el.get("SchemaVersion") or el.get("Version") or "unknown (assumed Proteus/DEXPI 1.x)"
        elif n == "Drawing" and not doc.drawing:
            doc.drawing = el.get("Name", "")
    if _local(root.tag) != "PlantModel":
        doc.warnings.append(f"Root element is '{_local(root.tag)}', expected 'PlantModel'. "
                            "This may be DEXPI 2.x, which this parser does not read.")

    def own_props(el) -> dict:
        out = {}
        for child in el:
            if _local(child.tag) == "GenericAttributes":
                for ga in child:
                    if ga.get("Name"):
                        out[ga.get("Name")] = ga.get("Value", "")
        if el.get("ComponentName"):
            out.setdefault("ComponentName", el.get("ComponentName"))
        return out

    def location(el):
        for child in el:
            if _local(child.tag) == "Position":
                for loc in child:
                    if _local(loc.tag) == "Location":
                        return {"x": loc.get("X"), "y": loc.get("Y")}
        return None

    def visit(el, parent):
        name = _local(el.tag)
        if name in ("Connection", "InformationFlow"):
            doc.rels.append(Rel(el.get("FromID"), el.get("ToID"),
                                "signal" if name == "InformationFlow" else "connection",
                                el.get("FromNode"), el.get("ToNode"),
                                source={"element": name, "in": parent.id if parent else None}))
            return
        if name == "Association":
            if parent and el.get("ItemID"):
                doc.rels.append(Rel(parent.id, el.get("ItemID"), "association:" + (el.get("Type") or ""),
                                    source={"element": name}))
            return
        if name in SKIP_TAGS:
            return
        cur = parent
        oid = el.get("ID")
        if oid:
            cls = el.get("ComponentClass", "")
            kind = _classify(name, cls)
            o = Obj(id=oid, cls=cls, kind=kind, tag=el.get("TagName"), properties=own_props(el),
                    parent_id=parent.id if parent else None,
                    source={"element": name, "drawing": doc.drawing, "location": location(el)})
            cp = next((c for c in el if _local(c.tag) == "ConnectionPoints"), None)
            if cp is not None:
                try:
                    o.n_nodes = int(cp.get("NumPoints") or len([n for n in cp if _local(n.tag) == "Node"]))
                except ValueError:
                    o.n_nodes = None
            if doc.add_object(o):
                if kind == "other":
                    doc.unsupported[cls or name] = doc.unsupported.get(cls or name, 0) + 1
                if parent is not None:
                    if parent.kind == "loop":
                        doc.rels.append(Rel(parent.id, oid, "loop"))
                    elif parent.kind in ("equipment", "segment"):
                        doc.rels.append(Rel(parent.id, oid, "owns"))
                cur = o
        for child in el:
            visit(child, cur)

    visit(root, None)
    return doc


def parse_neutral_json(path: str) -> Document:
    """Neutral format: what a PDF-review or Plant 3D adapter should emit."""
    d = json.load(open(path, encoding="utf-8"))
    doc = Document(file=os.path.basename(path), fmt="neutral-json", version=d.get("version", "n/a"),
                   parser="neutral-json", drawing=d.get("drawing", ""))
    for o in d.get("objects", []):
        doc.add_object(Obj(id=o["id"], cls=o.get("cls") or o.get("class", ""), kind=o["kind"], tag=o.get("tag"),
                           properties=o.get("properties", {}), parent_id=o.get("parent_id"),
                           n_nodes=o.get("n_nodes"), source=o.get("source", {}),
                           review_status=o.get("review_status", "extracted")))
    for r in d.get("relationships", []):
        doc.rels.append(Rel(r.get("source_id"), r.get("target_id"), r.get("type", "connection"),
                            r.get("source_node"), r.get("target_node"), r.get("confidence", 1.0),
                            r.get("provenance", "neutral-json"), r.get("source", {})))
    return doc


def load(path: str) -> Document:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return parse_neutral_json(path)
    if ext in (".xml", ".dexpi"):
        return parse_proteus(path)
    raise ValueError(f"Unsupported input '{ext}'. Use DEXPI 1.3 Proteus .xml or neutral .json")
