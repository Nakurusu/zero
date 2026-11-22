import pathlib

import pytest

from agent.storage import Repo, load_seed
from tests.server import HMAC_KEY, SEED_PATH, Server, seed_db

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def db_path(tmp_path):
    return seed_db(tmp_path / "test.db")


@pytest.fixture
def repo(db_path):
    r = Repo(db_path)
    yield r
    r.close()


@pytest.fixture
def real_server(tmp_path):
    s = Server(seed_db(tmp_path / "real.db")).start()
    yield s
    s.stop()


@pytest.fixture
def faulty_server(tmp_path):
    s = Server(seed_db(tmp_path / "faulty.db"), app="tests.faulty_app:app").start()
    yield s
    s.stop()
