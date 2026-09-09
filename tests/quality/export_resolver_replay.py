"""Export the official-domain resolver cache as a timestamp-free replay fixture.

The fixture lets ``tests/quality/tiering_eval.py`` re-adjudicate cached
relation graphs offline (no discovery searches, no fetches). Every
``resolved_at`` / ``observed_at`` / ``audited_at`` is rewritten to a fixed
epoch so the replay never expires and never depends on when the cache was
captured::

    python -m tests.quality.export_resolver_replay \
        --source runtime/official_domains.sqlite \
        --target tests/fixtures/official_domains_replay.sqlite
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

REPLAY_EPOCH = 1_800_000_000.0  # 2027-01-15T08:00:00Z; the replay clock sits just above it.
REPLAY_CLOCK = REPLAY_EPOCH + 60.0


def export_replay(source: Path, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    conn = sqlite3.connect(str(target))
    try:
        stems = conn.execute("SELECT COUNT(*) FROM entity_stem").fetchone()[0]
        conn.execute("UPDATE entity_stem SET resolved_at = ?", (REPLAY_EPOCH,))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        observations = 0
        if "candidate_observation" in tables:
            observations = conn.execute("SELECT COUNT(*) FROM candidate_observation").fetchone()[0]
            conn.execute("UPDATE candidate_observation SET observed_at = ?", (REPLAY_EPOCH,))
        if "pin_audit" in tables:
            conn.execute("UPDATE pin_audit SET audited_at = ?", (REPLAY_EPOCH,))
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()
    return {"stems": stems, "candidate_observations": observations, "replay_epoch": REPLAY_EPOCH}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="runtime/official_domains.sqlite")
    parser.add_argument("--target", default="tests/fixtures/official_domains_replay.sqlite")
    args = parser.parse_args()
    print(export_replay(Path(args.source), Path(args.target)))


if __name__ == "__main__":
    main()
