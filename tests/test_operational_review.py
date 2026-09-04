"""Stage 4 operational-review tests; all inputs and databases are temporary."""

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import content_snapshot
import database
import editor_preview
import init_db
import operational_review


def _snapshot_file(db_path, directory, name="snapshot.json"):
    snapshot = content_snapshot.export_content_snapshot(db_name=db_path)
    path = directory / name
    path.write_text(content_snapshot.content_snapshot_json(snapshot), encoding="utf-8")
    return path, snapshot


def _write_snapshot(path, snapshot):
    path.write_text(content_snapshot.content_snapshot_json(snapshot), encoding="utf-8")


def _without_preset(snapshot, code):
    altered = json.loads(json.dumps(snapshot))
    tables = altered["tables"]
    tables["Presets"] = [row for row in tables["Presets"] if row["short_code"] != code]
    for table in ("Preset_Blocks", "Preset_Block_Rows", "Quick_Type_Tokens"):
        tables[table] = [row for row in tables[table] if row["preset_code"] != code]
    return altered


def test_refuses_missing_or_malformed_snapshots_without_replacing_candidate(tmp_path, db):
    candidate = tmp_path / "candidate.json"
    candidate.write_text("keep", encoding="utf-8")
    with pytest.raises(operational_review.OperationalReviewError, match="Cannot read snapshot"):
        operational_review.generate(tmp_path / "missing.json", candidate)
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"format":"unknown"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported content snapshot format"):
        operational_review.generate(malformed, candidate)
    assert candidate.read_text(encoding="utf-8") == "keep"


def test_generation_hash_is_deterministic_and_matches_connection_preview(tmp_path, db):
    snapshot_path, snapshot = _snapshot_file(db, tmp_path)
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    artifact = operational_review.generate(snapshot_path, first)
    operational_review.generate(snapshot_path, second)
    assert first.read_bytes() == second.read_bytes()
    assert artifact["source_snapshot_sha256"] == hashlib.sha256(
        content_snapshot.content_snapshot_json(snapshot).encode("utf-8")
    ).hexdigest()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        expected = {
            row["short_code"]: editor_preview.render_preset_defaults(row["id"], conn=conn, strict=True)
            for row in conn.execute("SELECT id, short_code FROM Presets ORDER BY short_code")
        }
    finally:
        conn.close()
    assert [report["short_code"] for report in artifact["reports"]] == sorted(expected)
    for report in artifact["reports"]:
        preview = expected[report["short_code"]]
        for key in ("title", "clinical_info", "micro_plain", "conclusion_plain", "conflicts", "html"):
            assert report[key] == preview[key]


def test_comparison_reports_focused_changes_and_added_removed_presets(tmp_path, db):
    snapshot_path, snapshot = _snapshot_file(db, tmp_path)
    accepted_path, candidate_path = tmp_path / "accepted.json", tmp_path / "candidate.json"
    accepted = operational_review.generate(snapshot_path, accepted_path)
    changed = json.loads(json.dumps(snapshot))
    next(row for row in changed["tables"]["Blocks"] if row["key"] == "appendice")["micro_template"] += "\nCHANGED"
    changed_path = tmp_path / "changed.json"
    _write_snapshot(changed_path, changed)
    operational_review.generate(changed_path, candidate_path)
    result = operational_review.compare(candidate_path, accepted_path)
    assert "dai" in result["changed"]
    assert "CHANGED" in result["diffs"]["dai"]
    assert result["candidate_sha256"] == operational_review.canonical_hash(
        operational_review._load_artifact(candidate_path, "candidate")
    )

    removed_path = tmp_path / "removed.json"
    _write_snapshot(removed_path, _without_preset(snapshot, "dai"))
    operational_review.generate(removed_path, candidate_path)
    assert "dai" in operational_review.compare(candidate_path, accepted_path)["removed"]
    operational_review.generate(snapshot_path, candidate_path)
    operational_review.generate(removed_path, accepted_path)
    assert "dai" in operational_review.compare(candidate_path, accepted_path)["added"]
    assert accepted["reports"]


def test_render_failure_is_atomic_and_acceptance_is_explicit_and_hash_bound(tmp_path, db):
    snapshot_path, snapshot = _snapshot_file(db, tmp_path)
    candidate, accepted = tmp_path / "candidate.json", tmp_path / "accepted.json"
    candidate.write_text("old candidate", encoding="utf-8")
    invalid = json.loads(json.dumps(snapshot))
    next(row for row in invalid["tables"]["Blocks"] if row["key"] == "appendice")["micro_template"] = "{{ unknown_variable }}"
    invalid_path = tmp_path / "invalid.json"
    _write_snapshot(invalid_path, invalid)
    with pytest.raises(Exception):
        operational_review.generate(invalid_path, candidate)
    assert candidate.read_text(encoding="utf-8") == "old candidate"

    artifact = operational_review.generate(snapshot_path, candidate)
    accepted.write_text("old accepted", encoding="utf-8")
    with pytest.raises(operational_review.OperationalReviewError, match="differs"):
        operational_review.accept(candidate, accepted, "0" * 64)
    assert accepted.read_text(encoding="utf-8") == "old accepted"
    actual = operational_review.canonical_hash(artifact)
    assert operational_review.accept(candidate, accepted, actual) == actual
    assert operational_review._load_artifact(accepted, "accepted") == artifact


def test_operational_database_tripwire_and_paths_are_never_used(tmp_path, db, monkeypatch):
    snapshot_path, _snapshot = _snapshot_file(db, tmp_path)
    candidate, accepted = tmp_path / "candidate.json", tmp_path / "accepted.json"
    canary = tmp_path / "pathology.db"
    canary.write_bytes(b"operational-canary")
    checksum = hashlib.sha256(canary.read_bytes()).hexdigest()
    operational_path = Path("pathology.db").resolve()
    operational_checksum = hashlib.sha256(operational_path.read_bytes()).hexdigest()
    monkeypatch.setattr(database, "DB_NAME", str(canary))
    monkeypatch.setattr(init_db, "DB_NAME", str(canary))
    monkeypatch.setattr(
        database, "get_db_connection",
        lambda: (_ for _ in ()).throw(AssertionError("default database connector attempted")),
    )
    original_connect = sqlite3.connect

    def guarded_connect(path, *args, **kwargs):
        if Path(path).resolve(strict=False) in {canary.resolve(), operational_path}:
            raise AssertionError("operational database connection attempted")
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    original_setup = init_db.setup_database
    setup_paths = []

    def guarded_setup(db_name=None):
        assert db_name is not None
        assert Path(db_name).resolve(strict=False) != canary.resolve()
        setup_paths.append(Path(db_name))
        return original_setup(db_name=db_name)

    monkeypatch.setattr(init_db, "setup_database", guarded_setup)
    operational_review.generate(snapshot_path, candidate)
    assert setup_paths and all(path.parent != tmp_path for path in setup_paths)
    assert hashlib.sha256(canary.read_bytes()).hexdigest() == checksum
    assert hashlib.sha256(operational_path.read_bytes()).hexdigest() == operational_checksum
    with pytest.raises(operational_review.OperationalReviewError, match="different"):
        operational_review.generate(snapshot_path, snapshot_path)
    with pytest.raises(operational_review.OperationalReviewError, match="operational database"):
        operational_review.generate(snapshot_path, canary)
    with pytest.raises(operational_review.OperationalReviewError, match="different"):
        operational_review.compare(candidate, candidate)
