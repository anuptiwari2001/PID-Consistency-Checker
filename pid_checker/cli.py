from __future__ import annotations
import argparse
import os
import sys
import json
import yaml
from . import adapters
from .rules import Engine, apply_exceptions, inventory
from .llm import NoteExtractor, DEFAULT_MODEL, DEFAULT_HOST, heuristic_status
from .report import build_report, write_html, write_json, write_xlsx


def _yaml(path):
    if path and os.path.exists(path):
        return yaml.safe_load(open(path, encoding="utf-8")) or {}
    return {}


def cmd_check(a) -> int:
    doc = adapters.load(a.input)
    cfg = _yaml(a.config)
    exc = _yaml(a.exceptions).get("exceptions", [])
    ex = NoteExtractor(a.llm, a.model, a.host, cache_path=os.path.join(a.out, ".pid_llm_cache.json"))
    eng = Engine(doc, cfg, ex)
    findings = eng.run()
    apply_exceptions(doc, findings, exc)
    if a.explain and ex.use_llm:
        for f in findings:
            if f.status == "open":
                f.explanation = ex.explain(f"Rule {f.rule_id} ({f.title}). {f.message} Severity {f.severity}. "
                                           f"Source certainty: {f.certainty}.")
    os.makedirs(a.out, exist_ok=True)
    rep = build_report(doc, findings, inventory(doc, eng.G), ex)
    write_html(rep, os.path.join(a.out, "report.html"))
    write_xlsx(rep, os.path.join(a.out, "report.xlsx"))
    write_json(rep, os.path.join(a.out, "findings.json"))
    json.dump(doc.to_dict(), open(os.path.join(a.out, "neutral_model.json"), "w", encoding="utf-8"), indent=1, default=str)
    s = rep["summary"]
    print(f"{doc.file}: {len(doc.objects)} objects, {len(doc.rels)} relationships")
    print(f"LLM: {rep['input']['llm']}")
    print("Open findings:", s["open"], "| accepted:", s["accepted"])
    for f in findings:
        mark = "[accepted]" if f.status == "accepted" else ""
        print(f"  {f.severity.upper():8} {f.rule_id:14} {f.message} {mark}")
    print(f"Reports written to {a.out}/ (report.html, report.xlsx, findings.json, neutral_model.json)")
    return 1 if s["open"].get("error") and a.fail_on_error else 0


def cmd_llm_test(a) -> int:
    ex = NoteExtractor(a.llm, a.model, a.host, cache_path=None)
    print(f"Ollama model '{a.model}' reachable: {ex.use_llm}")
    for note in ["N2 SPARE, BLIND FLANGED", "Future tie-in by vendor", "Provision for second filter train", "Connect per rev C"]:
        print(f"  {note!r} -> {ex.classify(note)}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pid_checker", description="P&ID consistency checker")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("check", "llm-test"):
        sp = sub.add_parser(name)
        sp.add_argument("--llm", choices=["off", "auto", "on"], default="auto",
                        help="off=heuristics only, auto=use Ollama if running, on=require Ollama")
        sp.add_argument("--model", default=DEFAULT_MODEL)
        sp.add_argument("--host", default=DEFAULT_HOST)
        if name == "check":
            sp.add_argument("input", help="DEXPI 1.3 Proteus .xml or neutral .json")
            sp.add_argument("--config", default="config/project_rules.yaml")
            sp.add_argument("--exceptions", default="config/exceptions.yaml")
            sp.add_argument("--out", default="out")
            sp.add_argument("--explain", action="store_true", help="add short LLM explanations (slower)")
            sp.add_argument("--fail-on-error", action="store_true", help="exit code 1 if open errors (CI use)")
            sp.set_defaults(fn=cmd_check)
        else:
            sp.set_defaults(fn=cmd_llm_test)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
