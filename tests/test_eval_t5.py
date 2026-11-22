"""T5: evaluation repeatability — same fixed inputs/config, two isolated runs.

Uses the real API via the harness (small fixed subset per P5). Asserts both
runs execute all cases, produce complete per-turn records, aggregate checks
correctly, and remain distinguishable/comparable. Results need not match.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.harness import run_eval
from tests.server import requires_api

pytestmark = [requires_api, pytest.mark.real_api]

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "eval" / "cases.json"
CHECKS = ROOT / "eval" / "checks.json"
SEED = ROOT / "seeds" / "seed.json"
ARMS = ("explain-first", "practice-first")


def _load(run_dir: Path):
    recs = [json.loads(line) for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    checks = json.loads((run_dir / "check_results.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return recs, checks, summary


def _assert_complete(recs, checks, summary):
    assert len(recs) == 6
    expected = {("case-1", s, i) for s in ARMS for i in range(3)}
    assert {(r["case_id"], r["actual_strategy"], r["turn_index"]) for r in recs} == expected
    for r in recs:
        assert r["model_id"] and isinstance(r["params"], dict)
        assert r["planned_strategy"] in ARMS and r["actual_strategy"] in ARMS
        assert isinstance(r["planned_version"], int) and isinstance(r["actual_version"], int)
        assert r["status"] in ("completed", "failed", "interrupted")
        assert isinstance(r["input_tokens"], int) and isinstance(r["output_tokens"], int)
        assert isinstance(r["latency_ms"], int)
        assert isinstance(r["tool_count"], int)
        assert isinstance(r["checks"], list) and r["checks"]
    assert len(checks) == 22
    for strategy, agg in summary["strategies"].items():
        sc = [c for c in checks if c["strategy"] == strategy]
        assert agg["turns"] == sum(1 for r in recs if r["actual_strategy"] == strategy)
        for name, entry in agg["checks"].items():
            real = [c for c in sc if c["check_name"] == name]
            assert entry["total"] == len(real)
            assert entry["passed"] == sum(c["passed"] for c in real)


def test_t5_repeatable_eval(tmp_path):
    out = tmp_path / "runs"
    s1 = run_eval(CASES, CHECKS, SEED, out, case_filter=["case-1"])
    s2 = run_eval(CASES, CHECKS, SEED, out, case_filter=["case-1"])

    assert s1["run_id"] != s2["run_id"]
    for key in ("seed_manifest_hash", "model_id", "params", "strategy_versions",
                "checks_version", "cases_version", "case_ids"):
        assert s1[key] == s2[key], f"config drift in {key}"

    d1, d2 = out / s1["run_id"], out / s2["run_id"]
    assert d1 != d2 and (d1 / "run.db").exists() and (d2 / "run.db").exists()

    r1, c1, _ = _load(d1)
    r2, c2, _ = _load(d2)
    _assert_complete(r1, c1, s1)
    _assert_complete(r2, c2, s2)

    assert {r["run_id"] for r in r1} == {s1["run_id"]}
    assert {r["run_id"] for r in r2} == {s2["run_id"]}
    assert (d1 / "comparison.md").exists() and (d2 / "comparison.md").exists()

    totals = []
    for s in (s1, s2):
        totals.append({arm: (s["strategies"][arm]["input_tokens_total"],
                             s["strategies"][arm]["output_tokens_total"],
                             s["strategies"][arm]["tool_calls_total"]) for arm in ARMS})
    assert totals[0].keys() == totals[1].keys()  # comparable structure; values may differ
