import os
from pid_checker import adapters
from pid_checker.rules import Engine, apply_exceptions
from pid_checker.llm import NoteExtractor, heuristic_status
import yaml

ROOT = os.path.dirname(os.path.dirname(__file__))
SAMPLE = os.path.join(ROOT, "samples", "sample_proteus.xml")
CFG = yaml.safe_load(open(os.path.join(ROOT, "config", "project_rules.yaml")))
EXC = yaml.safe_load(open(os.path.join(ROOT, "config", "exceptions.yaml")))["exceptions"]


def run():
    doc = adapters.load(SAMPLE)
    f = Engine(doc, CFG, NoteExtractor("off", cache_path=None)).run()
    apply_exceptions(doc, f, EXC)
    return doc, f


def by(f, rid):
    return [x for x in f if x.rule_id == rid]


def test_nozzles():
    _, f = run()
    n = {x.tags[0]: x for x in by(f, "CONN-001")}
    assert set(n) == {"N2", "N3", "N4", "N5"}
    assert n["N3"].severity == "warning" and n["N3"].status == "open"
    assert n["N2"].severity == "review" and "spare" in n["N2"].message
    assert n["N4"].status == "accepted"
    assert n["N5"].severity == "review"


def test_model_rules():
    _, f = run()
    assert len(by(f, "DEXPI-NODE-001")) == 1
    assert len(by(f, "DEXPI-REF-001")) == 1
    assert len(by(f, "CONN-002")) == 1


def test_dead_end_and_island():
    _, f = run()
    assert [x.tags[0] for x in by(f, "CONN-003")] == ["XV-103"]
    assert len(by(f, "CONN-005")) == 1


def test_instruments_and_exceptions():
    _, f = run()
    orph = {x.tags[0]: x for x in by(f, "CONN-004")}
    assert set(orph) == {"PT-102", "PT-104"}          # PT-101 attached, PT-103 in loop
    assert orph["PT-102"].status == "accepted" and orph["PT-104"].status == "open"


def test_project_rules():
    _, f = run()
    assert len(by(f, "PRJ-TAG-001")) == 1
    missing = " ".join(x.message for x in by(f, "PRJ-REQ-001"))
    assert "V-101" in missing and "XV-103" in missing


def test_heuristics():
    assert heuristic_status("SPARE, BLIND FLANGED")["status"] == "spare"
    assert heuristic_status("Reserved for future")["status"] == "future"
    assert heuristic_status("see vendor")["status"] == "unknown"


def test_neutral_json_is_probable():
    doc = adapters.load(os.path.join(ROOT, "samples", "neutral_example.json"))
    f = Engine(doc, CFG, NoteExtractor("off", cache_path=None)).run()
    noz = by(f, "CONN-001")
    assert noz and noz[0].certainty == "probable"   # 0.58-confidence edge is ignored and unreviewed => probable
