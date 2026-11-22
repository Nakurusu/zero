from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@dataclass(frozen=True)
class ModelParams:
    model: str = "qwen3.8-flash"
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 0.9

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
        }


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    base_url: str = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    model_params: ModelParams = ModelParams()
    db_path: Path = Path("data/agent.db")
    heartbeat_interval: float = 1.0
    heartbeat_ttl: float = 5.0
    drain_deadline: float = 10.0
    slot_poll_interval: float = 0.15
    slot_wait_timeout: float = 30.0
    partial_flush_interval: float = 0.5
    history_token_budget: int = 3000
    history_max_turns: int = 20
    preselect_cap: int = 3
    max_tool_iterations: int = 8
    default_strategy: str = "explain-first"
    default_strategy_version: int = 1
    seed_hmac_key: str = "agent-seed-v1"
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls, dotenv: str | Path = ".env") -> Settings:
        _load_dotenv(Path(dotenv))
        e = os.environ.get

        def f(name: str, default: float) -> float:
            return float(e(name, default))

        def i(name: str, default: int) -> int:
            return int(e(name, default))

        return cls(
            api_key=e("QWEN_API_KEY", ""),
            base_url=e("AGENT_BASE_URL", cls.base_url),
            model_params=ModelParams(
                model=e("AGENT_MODEL", "qwen3.8-flash"),
                temperature=f("AGENT_TEMPERATURE", 0.7),
                max_tokens=i("AGENT_MAX_TOKENS", 2048),
                top_p=f("AGENT_TOP_P", 0.9),
            ),
            db_path=Path(e("AGENT_DB_PATH", "data/agent.db")),
            heartbeat_interval=f("AGENT_HEARTBEAT_INTERVAL", 1.0),
            heartbeat_ttl=f("AGENT_HEARTBEAT_TTL", 5.0),
            drain_deadline=f("AGENT_DRAIN_DEADLINE", 10.0),
            slot_poll_interval=f("AGENT_SLOT_POLL_INTERVAL", 0.15),
            slot_wait_timeout=f("AGENT_SLOT_WAIT_TIMEOUT", 30.0),
            partial_flush_interval=f("AGENT_PARTIAL_FLUSH_INTERVAL", 0.5),
            history_token_budget=i("AGENT_HISTORY_TOKEN_BUDGET", 3000),
            history_max_turns=i("AGENT_HISTORY_MAX_TURNS", 20),
            preselect_cap=i("AGENT_PRESELECT_CAP", 3),
            max_tool_iterations=i("AGENT_MAX_TOOL_ITERATIONS", 8),
            default_strategy=e("AGENT_DEFAULT_STRATEGY", "explain-first"),
            default_strategy_version=i("AGENT_DEFAULT_STRATEGY_VERSION", 1),
            seed_hmac_key=e("AGENT_SEED_HMAC_KEY", "agent-seed-v1"),
            host=e("AGENT_HOST", "127.0.0.1"),
            port=i("AGENT_PORT", 8000),
        )
