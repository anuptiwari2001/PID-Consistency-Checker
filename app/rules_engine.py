"""Deterministic checks. Every finding links to sheet, tag and evidence text."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml

from . import config
from .extract import Sheet, EQUIP_RE, INLINE_INST_RE, LINE_RE


def load_rules() -> dict:
    with open(config.RULES_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Finding:
    rule: str
    group: str
    severity: str
    sheet: str
    file: str
    page: int
    tag: str
    message: str
    evidence: str = ""

    def to_dict(self):
        return asdict(self)


def _f(sh: Sheet, rule, group, sev, tag, msg, ev=""):
    return Finding(rule, group, sev, sh.sheet_id, sh.file, sh.page, tag, msg, ev)


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


# ---------------------------------------------------------------- per sheet
def check_sheet(sh: Sheet, rules: dict) -> list[Finding]:
    out: list[Finding] = []
    if not sh.has_text:
        out.append(_f(sh, "NO_TEXT_LAYER", "Input quality", "High", "-",
                      "Page has no usable text layer (scanned or outlined). Nothing could be checked on this sheet.",
                      "Run OCR or re-plot from SPPID with text preserved."))
        return out

    ln_rules = rules.get("line_number", {})
    fluids = {x.upper() for x in ln_rules.get("allowed_fluids", []) or []}
    specs = {x.upper() for x in ln_rules.get("allowed_specs", []) or []}
    for ln in sh.line_nos:
        if not ln.complete:
            out.append(_f(sh, "INCOMPLETE_LINE_NUMBER", "Incomplete line", "High", ln.key,
                          "Line number has no piping spec.", ln.raw))
            continue
        if fluids and ln.fluid not in fluids:
            out.append(_f(sh, "FLUID_CODE_NOT_ALLOWED", "Project-rule violation", "Medium", ln.key,
                          f"Fluid code '{ln.fluid}' is not in the project list.", ln.raw))
        if specs and ln.spec not in specs:
            out.append(_f(sh, "SPEC_NOT_ALLOWED", "Project-rule violation", "Medium", ln.key,
                          f"Piping spec '{ln.spec}' is not in the project list.", ln.raw))

    allowed = {x.upper() for x in rules.get("instrument", {}).get("allowed_functions", []) or []}
    r_inst = float(rules.get("instrument", {}).get("search_radius_pt", 45))
    seen = set()
    for it in sh.instruments:
        if allowed and it.func not in allowed and (it.tag, "f") not in seen:
            seen.add((it.tag, "f"))
            out.append(_f(sh, "INSTRUMENT_FUNCTION_UNKNOWN", "Project-rule violation", "Low", it.tag,
                          f"Function '{it.func}' is not in the project instrument list (ISA-5.1 check).", it.tag))
        if sh.index and not sh.index.near(it.x, it.y, r_inst) and (it.tag, "o") not in seen:
            seen.add((it.tag, "o"))
            out.append(_f(sh, "ORPHANED_INSTRUMENT", "Orphaned instrument", "Review", it.tag,
                          f"No drawn line within {r_inst:.0f} pt of the instrument bubble. Verify it is connected to a line/signal.",
                          f"at ({it.x:.0f},{it.y:.0f})"))

    r_noz = float(rules.get("nozzle", {}).get("search_radius_pt", 45))
    for nz in sh.nozzles:
        if sh.index and not sh.index.near(nz.x, nz.y, r_noz):
            out.append(_f(sh, "UNCONNECTED_NOZZLE", "Unconnected nozzle", "Review", nz.tag,
                          f"No drawn line within {r_noz:.0f} pt of nozzle label. Verify it is connected or blanked.",
                          f"at ({nz.x:.0f},{nz.y:.0f})"))

    rx = (rules.get("drawing", {}) or {}).get("number_regex") or ""
    if rx and not re.search(rx, sh.titleblock):
        out.append(_f(sh, "DRAWING_NUMBER_FORMAT", "Project-rule violation", "Medium", "-",
                      "No drawing number matching the project format found in the title block.", sh.titleblock[:200]))
    return out


# ---------------------------------------------------------------- cross sheet
def check_batch(sheets: list[Sheet], rules: dict, notes_by_sheet: dict[str, list[dict]]) -> list[Finding]:
    out: list[Finding] = []
    usable = [s for s in sheets if s.has_text]

    # same line key, different spec / size / insulation across sheets
    by_key = defaultdict(list)
    for s in usable:
        for ln in s.line_nos:
            if ln.complete:
                by_key[ln.key].append((s, ln))
    size_sev = rules.get("line_number", {}).get("size_mismatch_severity", "Medium")
    for key, items in by_key.items():
        for attr, rule, sev, label in (("spec", "LINE_SPEC_MISMATCH", "High", "piping spec"),
                                       ("size", "LINE_SIZE_MISMATCH", size_sev, "size"),
                                       ("ins", "LINE_INSULATION_MISMATCH", "Medium", "insulation code")):
            vals = defaultdict(set)
            for s, ln in items:
                vals[(getattr(ln, attr) or "-")].add(s.sheet_id)
            if len(vals) > 1:
                desc = "; ".join(f"{v}: {', '.join(sorted(sh))}" for v, sh in sorted(vals.items()))
                first_sheet = items[0][0]
                out.append(_f(first_sheet, rule, "Cross-sheet inconsistency", sev, key,
                              f"Line {key} has different {label} on different sheets.", desc))

    # instrument tag repeated on different sheets
    inst_sheets = defaultdict(set)
    sheet_by_id = {s.sheet_id: s for s in sheets}
    for s in usable:
        for it in s.instruments:
            inst_sheets[it.tag].add(s.sheet_id)
    for tag, ids in inst_sheets.items():
        if len(ids) > 1:
            out.append(_f(sheet_by_id[sorted(ids)[0]], "INSTRUMENT_TAG_ON_MULTIPLE_SHEETS",
                          "Cross-sheet inconsistency", "Medium", tag,
                          "Instrument tag appears on more than one sheet (duplicate tag or missing continuation).",
                          ", ".join(sorted(ids))))

    # off-page connectors
    stems = {s.file: _norm(Path(s.file).stem) for s in usable}
    tb_by_file = defaultdict(str)
    for s in usable:
        tb_by_file[s.file] += " " + _norm(s.titleblock)
    for s in usable:
        for op in s.offpage:
            rn = _norm(op.ref)
            target_file = None
            for f, st in stems.items():
                if f == s.file:
                    continue
                if (rn and (rn in st or (len(st) >= 6 and st in rn))) or rn in tb_by_file[f]:
                    target_file = f
                    break
            if target_file is None:
                if rn and (rn in stems.get(s.file, "") or rn in tb_by_file[s.file]):
                    continue  # reference to itself
                out.append(_f(s, "OFFPAGE_TARGET_NOT_IN_BATCH", "Cross-sheet inconsistency", "Medium", op.ref,
                              f"Off-page connector {op.direction} {op.ref} points to a drawing that was not uploaded in this batch.",
                              op.text))
                continue
            if op.near_line_key:
                tgt_keys = {ln.key for t in usable if t.file == target_file for ln in t.line_nos}
                if op.near_line_key not in tgt_keys:
                    out.append(_f(s, "OFFPAGE_LINE_NOT_ON_TARGET", "Cross-sheet inconsistency", "High", op.near_line_key,
                                  f"Line {op.near_line_key} continues {op.direction} {op.ref}, but that line number is not on the target drawing ({target_file}).",
                                  op.text))

    # notes referencing tags not present anywhere in the batch
    all_tags = set(inst_sheets)
    for s in usable:
        all_tags |= {e.tag for e in s.equipment}
        all_tags |= {f"{l.fluid}-{l.seq}" for l in s.line_nos}
    for s in usable:
        llm_notes = notes_by_sheet.get(s.sheet_id)
        notes = llm_notes if llm_notes else [{"text": t, "tags": []} for t in s.notes]
        for n in notes:
            tags = set(t.upper() for t in n.get("tags", []))
            txt = n["text"].upper()
            tags |= {m["tag"] for m in EQUIP_RE.finditer(txt)}
            tags |= {f'{m["func"]}-{m["num"]}' for m in INLINE_INST_RE.finditer(EQUIP_RE.sub(" ", txt))
                     if m["func"] not in {"NOTE", "NOTES"}}
            for t in sorted(tags):
                if t not in all_tags:
                    out.append(_f(s, "NOTE_TAG_NOT_FOUND", "Cross-sheet inconsistency", "Review", t,
                                  "A note references this tag but it was not found on any uploaded sheet.", n["text"][:200]))
    return out
