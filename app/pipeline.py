from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from . import llm_notes
from .extract import extract_pdf
from .rules_engine import check_sheet, check_batch, load_rules, Finding

log = logging.getLogger("pid")


def analyze(pdf_paths: list[Path], use_llm: bool = True,
            progress: Callable[[int, int, str], None] | None = None) -> dict:
    rules = load_rules()
    use_llm = use_llm and bool(rules.get("llm", {}).get("enabled", True))
    total = len(pdf_paths)
    sheets, findings, errors = [], [], []
    notes_by_sheet: dict[str, list[dict]] = {}
    llm_note = "disabled"
    if use_llm:
        st = llm_notes.status()
        llm_note = f"Ollama model {st['selected']}" if st["reachable"] and st["selected"] else "requested but Ollama not reachable - regex fallback used"
        use_llm = bool(st["reachable"] and st["selected"])

    for i, p in enumerate(pdf_paths, 1):
        if progress:
            progress(i - 1, total, f"Reading {p.name}")
        try:
            file_sheets = extract_pdf(p)
        except Exception as e:  # corrupt/encrypted PDF must not kill the batch
            errors.append({"file": p.name, "error": str(e)})
            findings.append(Finding("PDF_UNREADABLE", "Input quality", "High", p.stem, p.name, 0, "-",
                                    f"Could not open PDF: {e}"))
            continue
        for sh in file_sheets:
            findings += check_sheet(sh, rules)
            if use_llm and sh.notes:
                if progress:
                    progress(i - 1, total, f"LLM notes {sh.sheet_id}")
                try:
                    notes_by_sheet[sh.sheet_id] = llm_notes.extract_notes(sh.notes)
                except Exception as e:
                    log.warning("LLM failed on %s: %s", sh.sheet_id, e)
                    llm_note += f" (failed on {sh.sheet_id})"
            sheets.append(sh)
        if progress:
            progress(i, total, f"Done {p.name}")

    findings += check_batch(sheets, rules, notes_by_sheet)

    index_lines, index_inst, notes = [], [], []
    for s in sheets:
        for ln in s.line_nos:
            index_lines.append([ln.key, ln.size, ln.spec or "", ln.ins or "", s.sheet_id, ln.raw])
        for it in s.instruments:
            index_inst.append([it.tag, s.sheet_id])
        if s.sheet_id in notes_by_sheet:
            for n in notes_by_sheet[s.sheet_id]:
                notes.append({"sheet": s.sheet_id, **n})
        else:
            for t in s.notes:
                notes.append({"sheet": s.sheet_id, "text": t})
    return {
        "files": [p.name for p in pdf_paths],
        "sheet_count": len(sheets),
        "llm_note": llm_note,
        "errors": errors,
        "findings": [f.to_dict() for f in findings],
        "index": {"lines": index_lines, "instruments": index_inst},
        "notes": notes,
    }
