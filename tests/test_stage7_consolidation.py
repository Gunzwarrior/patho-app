"""Stage 7 CP7: frozen cross-feature boundaries after consolidation."""

from pathlib import Path

import bulk_intake


def test_bulk_intake_has_one_canonical_review_and_pending_apply_workflow():
    """Do not retain compatibility aliases as a second operational API path."""
    assert callable(bulk_intake.prepare_bulk_review)
    assert callable(bulk_intake.apply_bulk_review)
    for obsolete_alias in (
        "prepare_bulk_preview",
        "prepare_batch_review",
        "apply_batch_review",
    ):
        assert not hasattr(bulk_intake, obsolete_alias)


def test_stage7_ui_modules_contain_no_sql_mutation_path():
    """Content and batch writes stay behind their reviewed service boundaries."""
    for path in ("pages/editor.py", "pages/bulk_intake.py"):
        source = Path(path).read_text(encoding="utf-8").upper()
        assert "SQLITE3" not in source
        assert ".EXECUTE(" not in source
        assert ".EXECUTEMANY(" not in source
        assert "EXECUTESCRIPT(" not in source
