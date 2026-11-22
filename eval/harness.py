"""Evaluation harness (E1-E6, D8): HTTP-driven, state-isolated, audit-direct.

Isolation (amendment 2): each run gets its own DB file + dedicated service
instance; within a run each strategy arm uses its own user (identical preset
learning_state). Same seed/manifest, same single model config source for both
arms and both runs; run_id distinguishes repetitions.

Usage:
    python eval/harness.py [--case case-1] [--run-id ID] [--out eval/runs]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from agent.config import Settings
from agent.storage import Repo, load_seed
from tests.server import Server, create_conversation, run_stream


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _latency_ms(turn: dict) -> int | None:
    if not turn.get("finished_at"):
        return None
    start = datetime.fromisoformat(turn["started_at"])
    end = datetime.fromisoformat(turn["finished_at"])
    return int((end - start).total_seconds() * 1000)


def run_checks(strategy: str, turn_index: int, tool_calls: list[dict], enabled: set[str]) -> list[dict]:
    results: list[dict] = []

    def add(name: str, passed: bool, detail: str) -> None:
        results.append({"check_name": name, "passed": int(passed), "detail": detail})

    def seqs(name: str) -> list[int]:
        return [t["call_seq"] for t in tool_calls if t["tool_name"] == name]

    if "only_allowed_tools_called" in enabled:
        bad = [t["tool_name"] for t in tool_calls if not t["was_allowed"]]
        add("only_allowed_tools_called", not bad, f"disallowed={bad}" if bad else "all allowed")

    if "practice_first_opens_with_question" in enabled and strategy == "practice-first" and turn_index == 0:
        ok = any(t["tool_name"] == "ask_question" and (t["result_ref"] or {}).get("valid")
                 for t in tool_calls)
        add("practice_first_opens_with_question", ok, f"tools={[t['tool_name'] for t in tool_calls]}")

    if "wrong_answer_followed_by_explanation" in enabled:
        wrong = [t["call_seq"] for t in tool_calls
                 if t["tool_name"] == "judge_answer" and (t["result_ref"] or {}).get("correct") is False]
        if not wrong:
            add("wrong_answer_followed_by_explanation", True, "vacuous: no wrong answer judged")
        else:
            exp = seqs("provide_explanation")
            ok = all(any(e > w for e in exp) for w in wrong)
            add("wrong_answer_followed_by_explanation", ok, f"wrong_seqs={wrong} explain_seqs={exp}")

    if "explain_first_explains_before_question" in enabled and strategy == "explain-first":
        exp, ask = seqs("provide_explanation"), seqs("ask_question")
        if not exp or not ask:
            add("explain_first_explains_before_question", True,
                f"vacuous: explain_seqs={exp} ask_seqs={ask}")
        else:
            add("explain_first_explains_before_question", min(exp) < min(ask),
                f"explain_seqs={exp} ask_seqs={ask}")

    if "ask_question_uses_bank_id" in enabled:
        asks = [t for t in tool_calls if t["tool_name"] == "ask_question"]
        if not asks:
            add("ask_question_uses_bank_id", True, "vacuous: no ask_question")
        else:
            ok = all((t["result_ref"] or {}).get("valid") for t in asks)
            add("ask_question_uses_bank_id", ok,
                f"question_ids={[ (t['result_ref'] or {}).get('question_id') for t in asks ]}")
    return results


def _aggregate(records: list[dict], checks: list[dict], strategy: str) -> dict:
    recs = [r for r in records if r["actual_strategy"] == strategy]
    statuses = {s: sum(1 for r in recs if r["status"] == s) for s in ("completed", "failed", "interrupted")}
    total_in = sum(r["input_tokens"] or 0 for r in recs)
    total_out = sum(r["output_tokens"] or 0 for r in recs)
    lat = [r["latency_ms"] for r in recs if r["latency_ms"] is not None]
    tool_names: dict[str, int] = {}
    for r in recs:
        for name in r["tool_names"]:
            tool_names[name] = tool_names.get(name, 0) + 1
    chk = [c for c in checks if c["strategy"] == strategy]
    per_check: dict[str, dict] = {}
    for c in chk:
        entry = per_check.setdefault(c["check_name"], {"passed": 0, "total": 0})
        entry["total"] += 1
        entry["passed"] += c["passed"]
    return {
        "turns": len(recs),
        "statuses": statuses,
        "fallback_turns": sum(1 for r in recs if r["fallback_reason"]),
        "input_tokens_total": total_in,
        "output_tokens_total": total_out,
        "input_tokens_avg": round(total_in / len(recs), 1) if recs else 0,
        "output_tokens_avg": round(total_out / len(recs), 1) if recs else 0,
        "latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else 0,
        "tool_calls_total": sum(r["tool_count"] for r in recs),
        "tool_calls_avg": round(sum(r["tool_count"] for r in recs) / len(recs), 2) if recs else 0,
        "tool_name_counts": dict(sorted(tool_names.items())),
        "checks": {k: {**v, "pass_rate": round(v["passed"] / v["total"], 3)} for k, v in sorted(per_check.items())},
    }


def _comparison_md(summary: dict) -> str:
    arms = list(summary["strategies"].keys())
    lines = [
        f"# 策略对比表 — {summary['run_id']}",
        "",
        f"- seed_manifest_hash: `{summary['seed_manifest_hash']}`",
        f"- model: `{summary['model_id']}` params: `{json.dumps(summary['params'], ensure_ascii=False)}`",
        f"- checks_version: {summary['checks_version']} cases_version: {summary['cases_version']}",
        "",
        "| 指标 | " + " | ".join(arms) + " |",
        "|---|" + "---|" * len(arms),
    ]
    rows = [
        ("轮次数", lambda a: a["turns"]),
        ("completed/failed/interrupted", lambda a: f"{a['statuses']['completed']}/{a['statuses']['failed']}/{a['statuses']['interrupted']}"),
        ("回退轮次", lambda a: a["fallback_turns"]),
        ("输入 token 总/均", lambda a: f"{a['input_tokens_total']} / {a['input_tokens_avg']}"),
        ("输出 token 总/均", lambda a: f"{a['output_tokens_total']} / {a['output_tokens_avg']}"),
        ("平均耗时 ms", lambda a: a["latency_ms_avg"]),
        ("工具调用总/均", lambda a: f"{a['tool_calls_total']} / {a['tool_calls_avg']}"),
        ("工具分布", lambda a: json.dumps(a["tool_name_counts"], ensure_ascii=False)),
    ]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(str(fn(summary["strategies"][s])) for s in arms) + " |")
    lines += ["", "## 行为检查通过率", "", "| 检查项 | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    check_names = sorted({c for s in arms for c in summary["strategies"][s]["checks"]})
    for name in check_names:
        cells = []
        for s in arms:
            entry = summary["strategies"][s]["checks"].get(name)
            cells.append(f"{entry['passed']}/{entry['total']} ({entry['pass_rate']:.0%})" if entry else "—")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## 观察", "", "（运行后撰写，见交付说明。）", ""]
    return "\n".join(lines)


def run_eval(
    cases_path: Path,
    checks_path: Path,
    seed_path: Path,
    out_dir: Path,
    run_id: str | None = None,
    case_filter: list[str] | None = None,
    settings: Settings | None = None,
) -> dict:
    settings = settings or Settings.from_env()
    assert settings.api_key, "API_KEY missing (.env)"
    cases_doc = json.loads(cases_path.read_text(encoding="utf-8"))
    checks_doc = json.loads(checks_path.read_text(encoding="utf-8"))
    cases = [c for c in cases_doc["cases"] if not case_filter or c["case_id"] in case_filter]
    assert cases, "no cases selected"
    enabled = {c["name"] for c in checks_doc["checks"]}
    arms = checks_doc["arms"]

    run_id = run_id or f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "run.db"

    repo = Repo(db_path)
    manifest = load_seed(repo, seed_path, settings.seed_hmac_key, cases_path)
    strategy_versions = {a["strategy"]: a["version"] for a in arms}
    repo.create_eval_run(run_id, manifest, settings.model_params.model,
                         settings.model_params.as_dict(), strategy_versions,
                         str(checks_doc["checks_version"]))
    repo.close()

    server = Server(db_path, extra_env={"AGENT_SLOT_WAIT_TIMEOUT": "10"}).start()
    records: list[dict] = []
    all_checks: list[dict] = []
    started = _now()
    try:
        marker_repo = Repo(db_path)
        for arm in arms:
            for case in cases:
                conv_id = create_conversation(server.base_url, arm["strategy"], arm["version"], arm["user"])
                for turn_index, user_message in enumerate(case["turns"]):
                    cmid = f"{run_id}:{case['case_id']}:{turn_index}"
                    events, turn_id = run_stream(server.base_url, conv_id, user_message, cmid)
                    terminal = events[-1]
                    marker_repo.con.execute(
                        "UPDATE turns SET eval_run_id=?, case_id=?, turn_index=? WHERE id=?",
                        (run_id, case["case_id"], turn_index, turn_id),
                    )
                    turn = marker_repo.get_turn(turn_id)
                    model_calls = marker_repo.get_model_calls(turn_id)
                    tool_calls = [
                        {**t, "args": json.loads(t["args"]),
                         "result_ref": json.loads(t["result_ref"]) if t["result_ref"] else None,
                         "was_allowed": bool(t["was_allowed"])}
                        for t in marker_repo.get_tool_calls(turn_id)
                    ]
                    checks = run_checks(turn["actual_strategy"] or turn["planned_strategy"],
                                        turn_index, tool_calls, enabled)
                    for c in checks:
                        c.update({"run_id": run_id, "case_id": case["case_id"],
                                  "turn_index": turn_index, "strategy": turn["actual_strategy"] or turn["planned_strategy"]})
                    marker_repo.insert_check_results(checks)
                    all_checks.extend(checks)
                    records.append({
                        "run_id": run_id, "case_id": case["case_id"], "turn_index": turn_index,
                        "conversation_id": conv_id, "turn_id": turn_id,
                        "user_message": user_message,
                        "status": turn["status"],
                        "terminal_event": terminal[0],
                        "planned_strategy": turn["planned_strategy"], "planned_version": turn["planned_version"],
                        "actual_strategy": turn["actual_strategy"], "actual_version": turn["actual_version"],
                        "fallback_reason": turn["fallback_reason"],
                        "error_code": turn["error_code"], "interrupt_reason": turn["interrupt_reason"],
                        "model_id": settings.model_params.model, "params": settings.model_params.as_dict(),
                        "input_tokens": sum(c["input_tokens"] or 0 for c in model_calls),
                        "output_tokens": sum(c["output_tokens"] or 0 for c in model_calls),
                        "model_call_count": len(model_calls),
                        "latency_ms": _latency_ms(turn),
                        "tool_count": len(tool_calls),
                        "tool_names": [t["tool_name"] for t in tool_calls],
                        "checks": checks,
                    })
                    print(f"[{run_id}] {arm['strategy']} {case['case_id']} t{turn_index}: "
                          f"{turn['status']} tools={[t['tool_name'] for t in tool_calls]}")
        marker_repo.finish_eval_run(run_id)
        marker_repo.close()
    finally:
        server.stop()

    summary = {
        "run_id": run_id,
        "seed_manifest_hash": manifest,
        "model_id": settings.model_params.model,
        "params": settings.model_params.as_dict(),
        "base_url": settings.base_url,
        "strategy_versions": strategy_versions,
        "checks_version": checks_doc["checks_version"],
        "cases_version": cases_doc["cases_version"],
        "case_ids": [c["case_id"] for c in cases],
        "started_at": started,
        "finished_at": _now(),
        "strategies": {a["strategy"]: _aggregate(records, all_checks, a["strategy"]) for a in arms},
    }
    (run_dir / "records.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    (run_dir / "check_results.json").write_text(
        json.dumps(all_checks, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "comparison.md").write_text(_comparison_md(summary), encoding="utf-8")
    print(f"run complete: {run_dir}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "eval" / "cases.json"))
    parser.add_argument("--checks", default=str(ROOT / "eval" / "checks.json"))
    parser.add_argument("--seed", default=str(ROOT / "seeds" / "seed.json"))
    parser.add_argument("--out", default=str(ROOT / "eval" / "runs"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--case", action="append", default=None, help="restrict to case_id (repeatable)")
    args = parser.parse_args()
    run_eval(Path(args.cases), Path(args.checks), Path(args.seed), Path(args.out),
             run_id=args.run_id, case_filter=args.case)


if __name__ == "__main__":
    main()
