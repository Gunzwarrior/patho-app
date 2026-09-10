"""Internal, guided Content Studio intents.

This module deliberately contains no database writes.  It is the small
translation boundary between future guided forms and the source-independent
candidate service in :mod:`content_changes`.  The Stage 5 package parser does
not import this module and therefore retains its intentionally narrower v1
contract.
"""

from __future__ import annotations

import copy

import content_changes
import content_snapshot


BASE_TABLES = frozenset(content_snapshot.BASE_TABLES)
CONFIGURATION_TABLES = frozenset(content_snapshot.RELATION_TABLES)
SUPPORTED_TABLES = BASE_TABLES | CONFIGURATION_TABLES


class StudioIntentError(ValueError):
    """A guided form supplied an incomplete or unsupported intent."""


def _copy_mapping(value, name):
    if not isinstance(value, dict):
        raise StudioIntentError(f"{name} must be an object.")
    return copy.deepcopy(value)


def operation(action, table, key, values=None):
    """Build one immutable-shape internal operation.

    ``action`` is one of create, update, archive, restore, delete, link,
    unlink, or reorder.  Keys are stable content keys (or the documented
    composite relationship keys), never SQLite row IDs.  The candidate engine
    resolves them only on its private copy and records physical row images.
    """
    if action not in {"create", "update", "archive", "restore", "delete", "link", "unlink", "reorder"}:
        raise StudioIntentError("Unsupported Content Studio action.")
    if table not in SUPPORTED_TABLES:
        raise StudioIntentError("Unsupported Content Studio table.")
    if not isinstance(key, (str, dict)):
        raise StudioIntentError("Content Studio keys must be stable keys.")
    if action in {"create", "update", "link", "reorder"}:
        values = _copy_mapping(values, "values")
    elif values is not None:
        raise StudioIntentError("This Content Studio action does not take values.")
    if table in BASE_TABLES and action in {"link", "unlink", "reorder"}:
        raise StudioIntentError("Base content cannot be linked or reordered.")
    if table in CONFIGURATION_TABLES and action in {"archive", "restore"}:
        raise StudioIntentError("Only base content has an archive state.")
    result = {"op": action, "table": table, "key": copy.deepcopy(key)}
    if values is not None:
        result["values"] = values
    return result


def case_preset_reference(case_id, before_preset_id, after_preset_id):
    """Prepare the narrowly allowed validated-Case Preset reference change.

    No case text is accepted here.  The candidate service independently checks
    the current before value and records the change in its dedicated audit
    table, rather than in content provenance.
    """
    if type(case_id) is not int or case_id <= 0:
        raise StudioIntentError("Case reference needs a valid Case ID.")
    if any(value is not None and (type(value) is not int or value <= 0)
           for value in (before_preset_id, after_preset_id)):
        raise StudioIntentError("Case reference needs valid Preset IDs.")
    return {"op": "case_preset_reference", "case_id": case_id,
            "before_preset_id": before_preset_id, "after_preset_id": after_preset_id}


def review(intents, base_snapshot_hash, *, summary="", db_name=None):
    """Prepare the same immutable review used by package imports and inverses."""
    if not isinstance(intents, list) or not intents:
        raise StudioIntentError("Prepare at least one Content Studio change.")
    return content_changes.review_candidate(
        copy.deepcopy(intents), base_snapshot_hash, summary=summary,
        db_name=db_name, internal=True,
    )
