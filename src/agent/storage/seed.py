from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from .repo import Repo, now_iso


def _hmac_id(key: str, content: str) -> str:
    return hmac.new(key.encode("utf-8"), content.encode("utf-8"), hashlib.sha256).hexdigest()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_manifest_hash(seed: dict, cases: dict | None, hmac_key: str) -> str:
    """Deterministic manifest over material HMAC ids + question bank + initial data + cases (D4.4)."""
    manifest = {
        "seed_version": seed["seed_version"],
        "materials": sorted(
            ({"id": _hmac_id(hmac_key, m["content"]), "content_hash": _sha256(m["content"])}
             for m in seed["materials"]),
            key=lambda x: x["id"],
        ),
        "questions": sorted(
            ({"id": q["id"], "hash": _sha256(json.dumps(q, ensure_ascii=False, sort_keys=True))}
             for q in seed["questions"]),
            key=lambda x: x["id"],
        ),
        "users": sorted(u["id"] for u in seed["users"]),
        "topics": sorted(t["id"] for t in seed["topics"]),
        "learning_state": sorted(
            ({"user_id": ls["user_id"], "topic_id": ls["topic_id"], "digest": ls["digest"]}
             for ls in seed["learning_state"]),
            key=lambda x: (x["user_id"], x["topic_id"]),
        ),
        "conversations": sorted(
            ({"id": c["id"], "strategy": [c["strategy_name"], c["strategy_version"]]}
             for c in seed.get("conversations", [])),
            key=lambda x: x["id"],
        ),
        "cases": cases,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256(canonical)


def load_seed(
    repo: Repo,
    seed_path: str | Path,
    hmac_key: str,
    cases_path: str | Path | None = None,
) -> str:
    """Idempotent seeding: content-hash insert-or-ignore, returns seed_manifest_hash."""
    seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    cases = json.loads(Path(cases_path).read_text(encoding="utf-8")) if cases_path else None

    for u in seed["users"]:
        repo.con.execute(
            "INSERT OR IGNORE INTO users(id, display_name) VALUES(?,?)", (u["id"], u["display_name"])
        )
    for t in seed["topics"]:
        repo.con.execute("INSERT OR IGNORE INTO topics(id, title) VALUES(?,?)", (t["id"], t["title"]))
    for m in seed["materials"]:
        repo.con.execute(
            "INSERT OR IGNORE INTO material_snippets(id, topic_id, title, content, keywords, content_hash, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (_hmac_id(hmac_key, m["content"]), m["topic_id"], m["title"], m["content"],
             json.dumps(m["keywords"], ensure_ascii=False), _sha256(m["content"]), now_iso()),
        )
    for q in seed["questions"]:
        repo.con.execute(
            "INSERT OR IGNORE INTO questions(id, topic_id, prompt, expected, acceptable, knowledge_point) "
            "VALUES(?,?,?,?,?,?)",
            (q["id"], q["topic_id"], q["prompt"], q["expected"],
             json.dumps(q["acceptable"], ensure_ascii=False), q["knowledge_point"]),
        )
    for ls in seed["learning_state"]:
        repo.con.execute(
            "INSERT OR IGNORE INTO learning_state(user_id, topic_id, digest, updated_at) VALUES(?,?,?,?)",
            (ls["user_id"], ls["topic_id"], json.dumps(ls["digest"], ensure_ascii=False), now_iso()),
        )
    for c in seed.get("conversations", []):
        repo.con.execute(
            "INSERT OR IGNORE INTO conversations(id, topic_id, user_id, strategy_name, strategy_version, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (c["id"], c["topic_id"], c["user_id"], c["strategy_name"], c["strategy_version"], now_iso()),
        )

    manifest_hash = compute_manifest_hash(seed, cases, hmac_key)
    repo.set_meta("seed_manifest_hash", manifest_hash)
    return manifest_hash
