from pathlib import Path

import pytest

from clipsnstrips.jobs import JobStore
from clipsnstrips.models import JobManifest, Segment, Stage
from clipsnstrips.review import record_approval, set_segment_approval


def test_job_round_trip_and_approval_gates(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    manifest = store.create(
        JobManifest(
            segments=[
                Segment(
                    id="one",
                    start=0,
                    end=20,
                    hook="Hook",
                    confidence=0.9,
                )
            ]
        )
    )
    with pytest.raises(PermissionError):
        manifest.require_approval("ingest")

    record_approval(
        store,
        manifest.id,
        purpose="ingest",
        reviewer="reviewer",
        approved=True,
        notes="Owned source file",
    )
    loaded = store.load(manifest.id)
    loaded.require_approval("ingest")
    assert loaded.stage is Stage.INGEST_AUTHORIZED

    set_segment_approval(store, manifest.id, {"one"})
    record_approval(
        store,
        manifest.id,
        purpose="spans",
        reviewer="reviewer",
        approved=True,
        notes="Timestamp reviewed in source context",
    )
    assert store.load(manifest.id).stage is Stage.SPANS_REVIEWED


def test_review_requires_auditable_notes(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    manifest = store.create()
    with pytest.raises(ValueError):
        record_approval(
            store,
            manifest.id,
            purpose="rights",
            reviewer="reviewer",
            approved=True,
            notes="",
        )
