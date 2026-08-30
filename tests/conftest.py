"""
PathoPilot — shared pytest fixtures.

The one rule everything here exists to enforce: **tests never read or
write the real pathology.db.** That file will eventually hold real
patient specimen data (the Cases table) — a test suite that touches it,
even by accident, is not a safety net, it's a hazard. Every fixture
below builds and points at a throwaway DB instead.

Two fixtures, two different needs:
- `db` (session-scoped): one seeded DB, built once, shared read-only
  across the whole test run. Use this for anything that only reads --
  rendering, grouping, quicktype, consistency, golden-output. This is
  the fixture almost every test should use.
- `mutable_db` (function-scoped): a fresh *copy* of that same seeded DB,
  private to one test function, auto-discarded afterward. Use this only
  for a test that actually writes (e.g. database.save_case) -- so one
  test's writes can never leak into another test's result.

Both work by pointing `database.DB_NAME` at a temp file instead of
"pathology.db". Two separate DB_NAME constants exist in this codebase
today (`database.DB_NAME` and `init_db.DB_NAME`, same default value,
not the same variable) -- `init_db.setup_database()` takes an explicit
`db_name` override specifically so building the test DB doesn't depend
on monkeypatching both globals in lockstep.
"""

import shutil
import pytest

import database
import init_db


@pytest.fixture(scope="session")
def db(tmp_path_factory):
    """Session-scoped: build the real schema + real seed_data content
    once, at a temp path, and point database.DB_NAME at it for the rest
    of the test process. Never restored -- this process never touches
    the real pathology.db in the first place, so there's nothing to
    restore back to."""
    db_path = tmp_path_factory.mktemp("pathopilot_db") / "test_pathology.db"
    init_db.setup_database(db_name=str(db_path))
    database.DB_NAME = str(db_path)
    return str(db_path)


@pytest.fixture
def mutable_db(db, tmp_path, monkeypatch):
    """Function-scoped: a private copy of the session DB for exactly one
    test. Use this instead of `db` whenever the test calls something
    that writes (database.save_case, or anything that INSERTs/UPDATEs) --
    `db`'s underlying file is shared across the whole session and every
    other test assumes it's still exactly what seed_data.seed_all()
    produced."""
    copy_path = tmp_path / "test_pathology_mutable.db"
    shutil.copy(db, copy_path)
    monkeypatch.setattr(database, "DB_NAME", str(copy_path))
    return str(copy_path)