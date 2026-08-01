from datetime import UTC, datetime
from pathlib import Path

import pytest

from clipsnstrips.jobs import JobStore, build_job_id
from clipsnstrips.models import Artifact, JobManifest, Segment, Stage
from clipsnstrips.review import (
    bypass_processing_gate,
    record_approval,
    set_segment_approval,
)


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


def test_new_render_path_replaces_segment_artifact() -> None:
    manifest = JobManifest()
    manifest.add_artifact(
        Artifact(
            kind="illustrated_video",
            path="art/segment.mp4",
            segment_id="segment",
        )
    )
    manifest.add_artifact(
        Artifact(
            kind="illustrated_video",
            path="art/segment/panel-video.mp4",
            segment_id="segment",
        )
    )
    assert [item.path for item in manifest.artifacts] == ["art/segment/panel-video.mp4"]


def test_no_approval_bypass_is_audited_and_selects_segments(
    tmp_path: Path,
) -> None:
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
    bypass_processing_gate(store, manifest.id, "ingest")
    bypass_processing_gate(store, manifest.id, "spans")

    loaded = store.load(manifest.id)
    assert loaded.approval_for("ingest").bypassed is True
    assert loaded.approval_for("spans").bypassed is True
    assert loaded.segments[0].approved is True
    with pytest.raises(PermissionError):
        bypass_processing_gate(store, manifest.id, "publish")


def test_job_id_uses_condensed_titles_and_timestamp() -> None:
    job_id = build_job_id(
        "THE WALLET! (2021) Official Trailer",
        "The Tim Dillon Show",
        timestamp=datetime(2026, 7, 31, 21, 22, 33, 123456, tzinfo=UTC),
    )
    assert job_id == ("thewallet2021officialtrailer_thetimdillonshow_20260731T212233123456Z")
    assert " " not in job_id
