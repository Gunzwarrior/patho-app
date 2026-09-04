"""Generate and compare human-reviewed operational content artifacts.

This is deliberately separate from pytest golden fixtures and never uses the
default operational database connection.  It accepts only an explicitly
provided content snapshot, restores it into a fresh temporary database, and
uses the connection-aware Editor preview renderer for every Preset.
"""

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

import content_snapshot
import database
import editor_preview
import grouping
import init_db
import rendering


ARTIFACT_FORMAT = "pathopilot-operational-review-v1"


class OperationalReviewError(ValueError):
    """A review input, validation, or explicit action was unsafe."""


def canonical_json(value):
    # Stable pretty-printing keeps the complete artifact readable in an
    # ordinary unified diff while still giving the hash one exact byte form.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def canonical_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _resolved_path(path):
    return Path(path).expanduser().resolve(strict=False)


def _check_paths(snapshot_path, candidate_path=None, accepted_path=None):
    named = [("snapshot", _resolved_path(snapshot_path))]
    if candidate_path is not None:
        named.append(("candidate", _resolved_path(candidate_path)))
    if accepted_path is not None:
        named.append(("accepted", _resolved_path(accepted_path)))
    for index, (name, path) in enumerate(named):
        for other_name, other_path in named[index + 1:]:
            if path == other_path:
                raise OperationalReviewError(f"{name.title()} and {other_name} paths must be different")
    protected = {_resolved_path(database.DB_NAME), _resolved_path(init_db.DB_NAME)}
    for name, path in named:
        if path in protected:
            raise OperationalReviewError(f"{name.title()} path may not be an operational database path")
    return dict(named)


def _read_json(path, description):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise OperationalReviewError(f"Cannot read {description}: {error}") from error


def _atomic_write(path, text):
    path = _resolved_path(path)
    if not path.parent.is_dir():
        raise OperationalReviewError(f"Output directory does not exist: {path.parent}")
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _validate_all_materialized_content(conn):
    """Strictly render every reachable Preset and every standalone template."""
    resolver = lambda shortcut: editor_preview._snippet_from_connection(conn, shortcut)
    label_lookup = lambda keys: editor_preview._label_from_connection(conn, keys)

    # Orphan Blocks and Field addenda must not escape validation merely
    # because no current Preset happens to reach them.
    for row in conn.execute("SELECT id FROM Blocks ORDER BY key"):
        block = database.get_block_on_connection(conn, row["id"])
        micro, conclusion = rendering.render_block(
            block, total_specimens=1, snippet_resolver=resolver, strict=True,
        )
        rendering.render_context_fragments(block, snippet_resolver=resolver, strict=True)
        grouping.render_conclusion_plain(
            [{"block": block, "overrides": {}, "conc_txt": conclusion}],
            resolver, label_lookup, True,
        )
        rendering.format_micro_plain([(block["name"], micro)])
    for field in conn.execute("SELECT key, type, default_value, conclusion_addendum_template FROM Fields ORDER BY key"):
        if field["conclusion_addendum_template"]:
            value = rendering.coerce_field_value(field["type"], field["default_value"])
            rendering.render_template(field["conclusion_addendum_template"], {"value": value}, resolver, True)


def _render_snapshot(snapshot):
    """Return fully rendered reports after isolated restore and validation."""
    with tempfile.TemporaryDirectory(prefix="pathopilot_operational_review_") as directory:
        temporary_db = Path(directory) / "candidate.db"
        # Both calls receive explicit paths.  No default DB connector is used.
        init_db.setup_database(db_name=str(temporary_db))
        restored, message = content_snapshot.restore_content_snapshot(snapshot, db_name=str(temporary_db))
        if not restored:
            raise OperationalReviewError(f"Snapshot validation/materialization failed: {message}")
        conn = sqlite3.connect(temporary_db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            _validate_all_materialized_content(conn)
            reports = []
            for preset in conn.execute("SELECT id, short_code FROM Presets ORDER BY short_code"):
                rendered = editor_preview.render_preset_defaults(preset["id"], conn=conn, strict=True)
                if rendered is None:
                    raise OperationalReviewError(f"Preset '{preset['short_code']}' disappeared during rendering")
                reports.append({
                    "short_code": preset["short_code"],
                    "title": rendered["title"],
                    "clinical_info": rendered["clinical_info"],
                    "micro_plain": rendered["micro_plain"],
                    "conclusion_plain": rendered["conclusion_plain"],
                    "conflicts": list(rendered["conflicts"]),
                    "html": rendered["html"],
                })
            return reports
        finally:
            conn.close()


def generate(snapshot_path, candidate_path):
    paths = _check_paths(snapshot_path, candidate_path)
    snapshot = _read_json(paths["snapshot"], "snapshot")
    content_snapshot.validate_content_snapshot(snapshot)
    artifact = {
        "format": ARTIFACT_FORMAT,
        "source_snapshot_sha256": hashlib.sha256(
            content_snapshot.content_snapshot_json(snapshot).encode("utf-8")
        ).hexdigest(),
        "reports": _render_snapshot(snapshot),
    }
    # Rendering and serialisation finish before the existing candidate is
    # replaced, so an error cannot leave a partial artifact behind.
    _atomic_write(paths["candidate"], canonical_json(artifact))
    return artifact


def _validate_artifact(artifact):
    expected = {"format", "source_snapshot_sha256", "reports"}
    if not isinstance(artifact, dict) or set(artifact) != expected or artifact.get("format") != ARTIFACT_FORMAT:
        raise OperationalReviewError("Unsupported operational review artifact")
    if not isinstance(artifact["source_snapshot_sha256"], str) or len(artifact["source_snapshot_sha256"]) != 64:
        raise OperationalReviewError("Artifact has an invalid source snapshot hash")
    expected_report = {"short_code", "title", "clinical_info", "micro_plain", "conclusion_plain", "conflicts", "html"}
    reports = artifact["reports"]
    if not isinstance(reports, list) or any(not isinstance(report, dict) or set(report) != expected_report for report in reports):
        raise OperationalReviewError("Artifact has invalid reports")
    codes = [report["short_code"] for report in reports]
    if codes != sorted(codes) or len(codes) != len(set(codes)):
        raise OperationalReviewError("Artifact reports are not uniquely sorted by short_code")


def _load_artifact(path, description):
    artifact = _read_json(path, description)
    _validate_artifact(artifact)
    return artifact


def compare(candidate_path, accepted_path):
    paths = _check_paths(candidate_path, accepted_path=accepted_path)
    candidate = _load_artifact(paths["snapshot"], "candidate artifact")
    accepted = _load_artifact(paths["accepted"], "accepted artifact")
    candidate_reports = {report["short_code"]: report for report in candidate["reports"]}
    accepted_reports = {report["short_code"]: report for report in accepted["reports"]}
    added = sorted(set(candidate_reports) - set(accepted_reports))
    removed = sorted(set(accepted_reports) - set(candidate_reports))
    unchanged, changed, diffs = [], [], {}
    for code in sorted(set(candidate_reports) & set(accepted_reports)):
        candidate_text = canonical_json(candidate_reports[code])
        accepted_text = canonical_json(accepted_reports[code])
        if candidate_text == accepted_text:
            unchanged.append(code)
        else:
            changed.append(code)
            diffs[code] = "".join(difflib.unified_diff(
                accepted_text.splitlines(keepends=True), candidate_text.splitlines(keepends=True),
                fromfile=f"accepted/{code}", tofile=f"candidate/{code}",
            ))
    return {
        "candidate_sha256": canonical_hash(candidate), "added": added,
        "removed": removed, "changed": changed, "unchanged": unchanged, "diffs": diffs,
    }


def accept(candidate_path, accepted_path, expected_candidate_hash):
    paths = _check_paths(candidate_path, accepted_path=accepted_path)
    candidate = _load_artifact(paths["snapshot"], "candidate artifact")
    actual_hash = canonical_hash(candidate)
    if actual_hash != expected_candidate_hash:
        raise OperationalReviewError("Candidate hash differs from the reviewed expected hash; compare it again before accepting")
    _atomic_write(paths["accepted"], canonical_json(candidate))
    return actual_hash


def _print_comparison(result):
    print(f"Candidate SHA-256: {result['candidate_sha256']}")
    for name in ("added", "removed", "changed", "unchanged"):
        values = result[name]
        print(f"{name.title()} ({len(values)}): {', '.join(values) if values else '(none)'}")
    for code in result["changed"]:
        print(result["diffs"][code], end="" if result["diffs"][code].endswith("\n") else "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate_parser = commands.add_parser("generate")
    generate_parser.add_argument("--snapshot", required=True)
    generate_parser.add_argument("--candidate", required=True)
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("--candidate", required=True)
    compare_parser.add_argument("--accepted", required=True)
    accept_parser = commands.add_parser("accept")
    accept_parser.add_argument("--candidate", required=True)
    accept_parser.add_argument("--accepted", required=True)
    accept_parser.add_argument("--expected-candidate-hash", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            artifact = generate(args.snapshot, args.candidate)
            print(f"Source snapshot SHA-256: {artifact['source_snapshot_sha256']}")
            print(f"Candidate artifact SHA-256: {canonical_hash(artifact)}")
            print(f"Generated {len(artifact['reports'])} reports: {args.candidate}")
        elif args.command == "compare":
            _print_comparison(compare(args.candidate, args.accepted))
        else:
            print(f"Accepted artifact SHA-256: {accept(args.candidate, args.accepted, args.expected_candidate_hash)}")
    except (OperationalReviewError, ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
