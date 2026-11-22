import pathlib

import pytest

from agent.storage import Repo, load_seed

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "seeds" / "seed.json"
HMAC_KEY = "agent-seed-v1"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    repo = Repo(path)
    load_seed(repo, SEED_PATH, HMAC_KEY)
    repo.close()
    return path


@pytest.fixture
def repo(db_path):
    r = Repo(db_path)
    yield r
    r.close()
