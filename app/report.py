from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Review": 3}
SEV_COLOR = {"High": "F8CBAD", "Medium": "FFE699", "Low": "DDEBF7", "Review": "E2EFDA"}


def _sorted(findings):
    return sorted(findings, key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["group"], f["sheet"], f["tag"]))


def write_json(job: dict, path: Path):
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")


def write_xlsx(job: dict, path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["P&ID Consistency Report"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Files", len(job["files"])])
    ws.append(["Sheets analysed", job["sheet_count"]])
    ws.append(["LLM used", job.get("llm_note", "no")])
    ws.append([])
    ws.append(["Severity", "Count"])
    c = Counter(f["severity"] for f in job["findings"])
    for s in ("High", "Medium", "Low", "Review"):
        ws.append([s, c.get(s, 0)])
    ws.append([])
    ws.append(["Group", "Count"])
    for g, n in Counter(f["group"] for f in job["findings"]).most_common():
        ws.append([g, n])
    ws.column_dimensions["A"].width = 38

    ws = wb.create_sheet("Findings")
    hdr = ["Severity", "Group", "Rule", "File", "Page", "Sheet", "Tag", "Message", "Evidence"]
    ws.append(hdr)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9D9D9")
    for f in _sorted(job["findings"]):
        ws.append([f["severity"], f["group"], f["rule"], f["file"], f["page"], f["sheet"], f["tag"], f["message"], f["evidence"]])
        ws.cell(ws.max_row, 1).fill = PatternFill("solid", fgColor=SEV_COLOR.get(f["severity"], "FFFFFF"))
    for col, w in zip("ABCDEFGHI", (10, 26, 30, 28, 6, 26, 18, 70, 50)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    ws = wb.create_sheet("Line index")
    ws.append(["Line key", "Size", "Spec", "Insulation", "Sheet", "As written"])
    for r in job["index"]["lines"]:
        ws.append(r)
    ws = wb.create_sheet("Instrument index")
    ws.append(["Tag", "Sheet"])
    for r in job["index"]["instruments"]:
        ws.append(r)
    ws = wb.create_sheet("Notes")
    ws.append(["Sheet", "Category", "Note", "Requirement (LLM)", "Tags"])
    for r in job["notes"]:
        ws.append([r["sheet"], r.get("category", ""), r["text"], r.get("requirement", ""), ", ".join(r.get("tags", []))])
    wb.save(path)


def write_html(job: dict, path: Path):
    c = Counter(f["severity"] for f in job["findings"])
    rows = []
    for f in _sorted(job["findings"]):
        rows.append(
            f"<tr class='{f['severity']}'><td>{f['severity']}</td><td>{html.escape(f['group'])}</td>"
            f"<td>{html.escape(f['sheet'])} (p{f['page']})</td><td>{html.escape(f['tag'])}</td>"
            f"<td>{html.escape(f['message'])}<div class='ev'>{html.escape(f['evidence'])}</div></td></tr>")
    path.write_text(f"""<!doctype html><meta charset=utf-8><title>P&ID Consistency Report</title>
<style>body{{font:14px Segoe UI,Arial;margin:24px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px;vertical-align:top}}th{{background:#eee}}
.High td:first-child{{background:#f8cbad}}.Medium td:first-child{{background:#ffe699}}
.Low td:first-child{{background:#ddebf7}}.Review td:first-child{{background:#e2efda}}
.ev{{color:#666;font-size:12px;margin-top:3px}}</style>
<h2>P&amp;ID Consistency Report</h2>
<p>{len(job['files'])} files, {job['sheet_count']} sheets. High: {c.get('High',0)} | Medium: {c.get('Medium',0)} |
Low: {c.get('Low',0)} | Review: {c.get('Review',0)}</p>
<table><tr><th>Severity</th><th>Group</th><th>Sheet</th><th>Tag</th><th>Finding</th></tr>{''.join(rows)}</table>""",
                    encoding="utf-8")
