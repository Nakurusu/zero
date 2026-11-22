"""Subprocess server harness + SSE client helpers for real-API tests."""
from __future__ import annotations

import json
import os
import pathlib
import queue
import socket
import subprocess
import sys
import threading
import time

import httpx
import pytest

from agent.config import Settings
from agent.storage import Repo, load_seed

ROOT = pathlib.Path(__file__).resolve().parents[1]
LONG_PROMPT = "请非常详细地逐步讲解配方法、因式分解法、求根公式和韦达定理，并各举一个例子。"
SEED_PATH = ROOT / "seeds" / "seed.json"
HMAC_KEY = "agent-seed-v1"

requires_api = pytest.mark.skipif(not Settings.from_env().api_key, reason="API_KEY missing in env/.env")


def seed_db(path) -> pathlib.Path:
    repo = Repo(path)
    load_seed(repo, SEED_PATH, HMAC_KEY)
    repo.close()
    return pathlib.Path(path)


class Server:
    def __init__(self, db_path, app: str = "agent.asgi:app", extra_env: dict | None = None):
        self.db_path = pathlib.Path(db_path)
        self.app = app
        self.extra_env = extra_env or {}
        self.proc: subprocess.Popen | None = None
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> Server:
        env = {
            **os.environ,
            "AGENT_DB_PATH": str(self.db_path),
            "AGENT_SLOT_WAIT_TIMEOUT": "2",
            "AGENT_HEARTBEAT_TTL": "2",
            "AGENT_HEARTBEAT_INTERVAL": "0.5",
            "AGENT_PARTIAL_FLUSH_INTERVAL": "0.3",
            **self.extra_env,
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", self.app, "--host", "127.0.0.1", "--port", str(self.port)],
            env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(80):
            try:
                if httpx.get(f"{self.base_url}/healthz", timeout=1).status_code == 200:
                    return self
            except Exception:
                time.sleep(0.25)
        self.kill()
        raise RuntimeError("server did not start")

    def kill(self) -> None:
        if self.proc:
            self.proc.kill()
            self.proc.wait(timeout=10)
            self.proc = None

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
            self.proc = None


def sse_events(resp):
    et = None
    for line in resp.iter_lines():
        if line.startswith("event: "):
            et = line[7:]
        elif line.startswith("data: "):
            yield et, json.loads(line[6:])


def _stream_thread(base_url, conv_id, payload, q):
    try:
        with httpx.Client(timeout=180) as c:
            with c.stream("POST", f"{base_url}/conversations/{conv_id}/messages", json=payload) as r:
                q.put(("http", r.status_code))
                if r.status_code != 200:
                    q.put(("body", r.read().decode()))
                    return
                for et, data in sse_events(r):
                    q.put((et, data))
    except Exception as e:
        q.put(("exception", str(e)))


def run_stream(base_url, conv_id, content, client_msg_id, mid_delta=None):
    """Drive one POST messages SSE to its terminal event; returns (events, turn_id)."""
    q: queue.Queue = queue.Queue()
    payload = {"content": content, "client_msg_id": client_msg_id}
    threading.Thread(target=_stream_thread, args=(base_url, conv_id, payload, q), daemon=True).start()
    events, turn_id, fired = [], None, False
    while True:
        et, data = q.get(timeout=180)
        events.append((et, data))
        if et == "turn_started":
            turn_id = data["turn_id"]
        if et == "delta" and mid_delta and not fired:
            fired = True
            mid_delta(turn_id)
        if et in ("done", "error", "interrupted", "exception", "body"):
            break
    return events, turn_id


def wait_turn(base_url, conv_id, turn_id, timeout=150) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hist = httpx.get(f"{base_url}/conversations/{conv_id}/messages", timeout=10).json()
        tb = next(t for t in hist["turns"] if t["turn_id"] == turn_id)
        if tb["status"] != "running":
            return tb
        time.sleep(0.5)
    raise TimeoutError(f"turn {turn_id} still running after {timeout}s")


def create_conversation(base_url, strategy="explain-first", version=1, user="u-demo") -> str:
    r = httpx.post(f"{base_url}/conversations", timeout=10,
                   json={"topic_id": "t-quadratic", "user_id": user,
                         "strategy_name": strategy, "strategy_version": version})
    assert r.status_code == 201, r.text
    return r.json()["id"]
