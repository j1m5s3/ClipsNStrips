from __future__ import annotations

import logging

from clipsnstrips.jobs import JobStore
from clipsnstrips.models import Approval, RightsState, Stage

logger = logging.getLogger(__name__)


def record_approval(
    store: JobStore,
    job_id: str,
    *,
    purpose: str,
    reviewer: str,
    approved: bool,
    notes: str,
) -> None:
    if not reviewer.strip() or not notes.strip():
        raise ValueError("Reviewer and notes are required for an auditable decision")
    manifest = store.load(job_id)
    manifest.approvals.append(
        Approval(
            purpose=purpose,
            reviewer=reviewer,
            approved=approved,
            notes=notes,
        )
    )
    if purpose == "rights":
        manifest.rights_state = RightsState.APPROVED if approved else RightsState.BLOCKED
    elif purpose == "ingest" and not approved:
        manifest.rights_state = RightsState.BLOCKED
    if purpose == "ingest" and approved:
        manifest.stage = Stage.INGEST_AUTHORIZED
    elif purpose == "spans" and approved:
        if not any(segment.approved for segment in manifest.segments):
            raise ValueError("Approve at least one segment before approving spans")
        manifest.stage = Stage.SPANS_REVIEWED
    elif purpose == "output" and approved:
        manifest.stage = Stage.OUTPUT_REVIEWED
    store.save(manifest)
    logger.info(
        "Recorded approval job_id=%s purpose=%s approved=%s",
        job_id,
        purpose,
        approved,
    )


def set_segment_approval(
    store: JobStore,
    job_id: str,
    segment_ids: set[str],
) -> None:
    manifest = store.load(job_id)
    known = {segment.id for segment in manifest.segments}
    unknown = segment_ids - known
    if unknown:
        raise KeyError(f"Unknown segment IDs: {', '.join(sorted(unknown))}")
    for segment in manifest.segments:
        segment.approved = segment.id in segment_ids
    store.save(manifest)
    logger.info(
        "Updated segment selection job_id=%s selected_count=%d",
        job_id,
        len(segment_ids),
    )
