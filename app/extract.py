"""PDF -> structured sheet objects (text layer + vector lines). No LLM here."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class TextLine:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self): return (self.x0 + self.x1) / 2
    @property
    def cy(self): return (self.y0 + self.y1) / 2


@dataclass
class LineNo:
    raw: str
    size: str
    fluid: str
    seq: str
    spec: str | None
    ins: str | None
    x: float
    y: float

    @property
    def key(self): return f"{self.fluid}-{self.seq}"
    @property
    def complete(self): return bool(self.spec)


@dataclass
class Instrument:
    tag: str
    func: str
    num: str
    x: float
    y: float


@dataclass
class Tagged:
    tag: str
    x: float
    y: float


@dataclass
class OffPage:
    direction: str
    ref: str
    text: str
    x: float
    y: float
    near_line_key: str | None = None


@dataclass
class Sheet:
    sheet_id: str
    file: str
    page: int
    width: float
    height: float
    has_text: bool
    lines: list[TextLine] = field(default_factory=list)
    titleblock: str = ""
    line_nos: list[LineNo] = field(default_factory=list)
    instruments: list[Instrument] = field(default_factory=list)
    equipment: list[Tagged] = field(default_factory=list)
    nozzles: list[Tagged] = field(default_factory=list)
    offpage: list[OffPage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    index: "SegIndex | None" = None


# ---------------------------------------------------------------- geometry
class SegIndex:
    """Grid index of line segments for fast 'is there a drawn line near here' queries."""

    def __init__(self, segs, cell=50.0):
        self.cell = cell
        self.segs = segs
        self.grid: dict[tuple[int, int], list[int]] = {}
        for i, (x0, y0, x1, y1) in enumerate(segs):
            for cx in range(int(min(x0, x1) // cell), int(max(x0, x1) // cell) + 1):
                for cy in range(int(min(y0, y1) // cell), int(max(y0, y1) // cell) + 1):
                    self.grid.setdefault((cx, cy), []).append(i)

    @staticmethod
    def _dist(px, py, s):
        x0, y0, x1, y1 = s
        dx, dy = x1 - x0, y1 - y0
        if dx == 0 and dy == 0:
            return math.hypot(px - x0, py - y0)
        t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))

    def near(self, x, y, radius) -> bool:
        c = self.cell
        seen = set()
        for cx in range(int((x - radius) // c), int((x + radius) // c) + 1):
            for cy in range(int((y - radius) // c), int((y + radius) // c) + 1):
                for i in self.grid.get((cx, cy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    if self._dist(x, y, self.segs[i]) <= radius:
                        return True
        return False


# ---------------------------------------------------------------- regexes
_SIZE = r'(?P<size>\d{1,2}(?:-\d/\d|\.\d+)?|\d/\d)\s*(?:["\u201d\u2033]|IN\b|NB\b)?'
LINE_RE = re.compile(
    r'(?<![\w/])' + _SIZE +
    r'\s*-\s*(?P<fluid>[A-Z]{1,4})\s*-\s*(?P<seq>\d{3,6})\s*-\s*(?P<spec>[A-Z0-9]{2,6})'
    r'(?:\s*-\s*(?P<ins>[A-Z]{1,3}))?(?![\w-])'
)
LINE_PARTIAL_RE = re.compile(
    r'(?<![\w/])' + _SIZE + r'\s*-\s*(?P<fluid>[A-Z]{1,4})\s*-\s*(?P<seq>\d{3,6})(?![\w-])'
)
INLINE_INST_RE = re.compile(r'(?<![A-Z0-9])(?P<func>[A-Z]{2,5})\s?-?\s?(?P<num>\d{3,5}[A-Z]?)(?![A-Z0-9])')
FUNC_ONLY_RE = re.compile(r'^[A-Z]{2,5}$')
NUM_ONLY_RE = re.compile(r'^\d{3,5}[A-Z]?$')
EQUIP_RE = re.compile(r'(?<![A-Z0-9-])(?P<tag>[PVEKCTHRDFMS]-\d{3,4}(?:[A-Z](?:/[A-Z])?)?)(?![A-Z0-9-])')
NOZZLE_RE = re.compile(r'^N\d{1,2}[A-Z]?$')
OFFPAGE_RE = re.compile(
    r'\b(?P<dir>TO|FROM)\b[\s:]*(?:DWG|DRAWING|P&ID|PID|SHEET)?\.?\s*(?:NO\.?|#)?\s*[:\-]?\s*'
    r'(?P<ref>[A-Z0-9][A-Z0-9\-_/\.]{5,})', re.I)
STOP = {"NOTE", "NOTES", "REV", "DWG", "SHEET", "ITEM", "SCALE", "DATE", "NOZZLE", "PAGE", "SIZE",
        "AREA", "UNIT", "TAG", "LINE", "DESC", "PROJ", "JOB", "DOC", "PIPE", "SPEC", "CLASS",
        "PID", "PSIG", "BARG", "KPAG", "DEG", "MAX", "MIN", "TYPE", "ISSUED", "DRAWN", "CHKD", "APPD"}
NOTE_START_RE = re.compile(r'^(?:GENERAL\s+)?NOTES?\b[:.]?', re.I)
NOTE_NUM_RE = re.compile(r'^\d{1,2}[.)]\s+\S')


def _mask(text: str, spans):
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            chars[i] = " "
    return "".join(chars)


def _read_lines(page) -> list[TextLine]:
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for l in b["lines"]:
            txt = " ".join(s["text"].strip() for s in l["spans"] if s["text"].strip())
            if txt:
                x0, y0, x1, y1 = l["bbox"]
                out.append(TextLine(txt, x0, y0, x1, y1))
    return out


def _segments(page, cap=300_000):
    segs = []
    try:
        for dr in page.get_drawings():
            for it in dr["items"]:
                if it[0] == "l":
                    segs.append((it[1].x, it[1].y, it[2].x, it[2].y))
                    if len(segs) >= cap:
                        return segs
    except Exception:
        pass
    return segs


def _near_line_key(lines_nos: list[LineNo], x, y, maxd=220):
    best, bd = None, maxd
    for ln in lines_nos:
        d = math.hypot(ln.x - x, ln.y - y)
        if d < bd:
            best, bd = ln.key, d
    return best


def extract_sheet(page, file: str, page_no: int, sheet_id: str) -> Sheet:
    w, h = page.rect.width, page.rect.height
    lines = _read_lines(page)
    sh = Sheet(sheet_id=sheet_id, file=file, page=page_no, width=w, height=h,
               has_text=len(lines) > 5, lines=lines)
    sh.titleblock = " ".join(l.text for l in lines if l.cx > 0.55 * w and l.cy > 0.75 * h)
    sh.index = SegIndex(_segments(page))

    # notes (found first so their tags are not mistaken for drawn tags)
    note_ids: set[int] = set()
    note_idx = [i for i, l in enumerate(lines) if NOTE_NUM_RE.match(l.text) and len(l.text) > 20]
    for i in note_idx:
        txt = lines[i].text
        cur = lines[i]
        note_ids.add(id(cur))
        for _ in range(3):
            nxt = [o for o in lines if 0 <= o.y0 - cur.y1 <= 14 and abs(o.x0 - cur.x0) < 8
                   and not NOTE_NUM_RE.match(o.text)]
            if not nxt:
                break
            cur = min(nxt, key=lambda o: o.y0)
            note_ids.add(id(cur))
            txt += " " + cur.text
        sh.notes.append(txt)

    seen_inst = set()
    for l in lines:
        if id(l) in note_ids:
            continue
        in_tb = l.cx > 0.55 * w and l.cy > 0.80 * h
        text = l.text.upper()
        spans = []
        for m in LINE_RE.finditer(text):
            sh.line_nos.append(LineNo(m.group(0), m["size"], m["fluid"], m["seq"], m["spec"], m["ins"], l.cx, l.cy))
            spans.append(m.span())
        text = _mask(text, spans)
        spans = []
        for m in LINE_PARTIAL_RE.finditer(text):
            sh.line_nos.append(LineNo(m.group(0), m["size"], m["fluid"], m["seq"], None, None, l.cx, l.cy))
            spans.append(m.span())
        text = _mask(text, spans)

        if NOZZLE_RE.match(text.strip()):
            sh.nozzles.append(Tagged(text.strip(), l.cx, l.cy))
            continue
        if in_tb:
            continue
        for m in EQUIP_RE.finditer(text):
            sh.equipment.append(Tagged(m["tag"], l.cx, l.cy))
        text_wo_eq = EQUIP_RE.sub(" ", text)
        if len(text_wo_eq.strip()) > 14:   # bubbles are short; long text = descriptions/notes
            continue
        for m in INLINE_INST_RE.finditer(text_wo_eq):
            f, n = m["func"], m["num"]
            if f in STOP:
                continue
            tag = f"{f}-{n}"
            if (tag, round(l.cx), round(l.cy)) not in seen_inst:
                seen_inst.add((tag, round(l.cx), round(l.cy)))
                sh.instruments.append(Instrument(tag, f, n, l.cx, l.cy))

    # stacked bubbles: "PT" above "1001"
    lines_ok = [l for l in lines if id(l) not in note_ids and not (l.cx > 0.55 * w and l.cy > 0.80 * h)]
    funcs = [l for l in lines_ok if FUNC_ONLY_RE.match(l.text.strip().upper()) and l.text.strip().upper() not in STOP]
    nums = [l for l in lines_ok if NUM_ONLY_RE.match(l.text.strip())]
    for f in funcs:
        for n in nums:
            if abs(f.cx - n.cx) <= 15 and -2 <= n.y0 - f.y1 <= 20:
                ft, nt = f.text.strip().upper(), n.text.strip().upper()
                tag = f"{ft}-{nt}"
                sh.instruments.append(Instrument(tag, ft, nt, (f.cx + n.cx) / 2, (f.cy + n.cy) / 2))
                break

    # off-page connectors (also try joining "TO" with the line just below it)
    for i, l in enumerate(lines):
        cands = [l.text]
        if re.match(r'^(TO|FROM)\b', l.text.strip(), re.I):
            below = [o for o in lines if 0 <= o.y0 - l.y1 <= 25 and abs(o.cx - l.cx) < 80 and o is not l]
            below.sort(key=lambda o: o.y0 - l.y1)
            if below:
                cands.append(l.text + " " + below[0].text)
        for c in cands:
            m = OFFPAGE_RE.search(c)
            if m and re.search(r'\d', m["ref"]):
                sh.offpage.append(OffPage(m["dir"].upper(), m["ref"].upper().rstrip(".-"), c, l.cx, l.cy))
                break
    for op in sh.offpage:
        op.near_line_key = _near_line_key(sh.line_nos, op.x, op.y)

    return sh


def extract_pdf(path: Path) -> list[Sheet]:
    doc = fitz.open(path)
    sheets = []
    for i, page in enumerate(doc):
        sid = path.stem if len(doc) == 1 else f"{path.stem}_p{i + 1}"
        sheets.append(extract_sheet(page, path.name, i + 1, sid))
    doc.close()
    return sheets
