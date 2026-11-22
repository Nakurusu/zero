"""Initialize/seed the database (idempotent). Prints seed_manifest_hash."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.config import Settings
from agent.storage import Repo, load_seed


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(settings.db_path))
    parser.add_argument("--seed", default=str(Path(__file__).resolve().parents[1] / "seeds" / "seed.json"))
    parser.add_argument("--cases", default=None, help="eval cases JSON to include in the manifest")
    args = parser.parse_args()
    repo = Repo(args.db)
    manifest = load_seed(repo, args.seed, settings.seed_hmac_key, args.cases)
    repo.close()
    print(f"seeded {args.db}\nseed_manifest_hash={manifest}")


if __name__ == "__main__":
    main()
