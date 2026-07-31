from __future__ import annotations

import logging

from clipsnstrips.models import RightsState, VideoCandidate

HIGH_RISK_CATEGORIES = {"10", "30"}  # Music, Movies
logger = logging.getLogger(__name__)


def classify_candidate(
    candidate: VideoCandidate,
    owned_channel_ids: set[str] | None = None,
) -> VideoCandidate:
    owned_channel_ids = owned_channel_ids or set()
    score = 0
    reasons: list[str] = []

    if candidate.channel_id in owned_channel_ids:
        reasons.append("Source channel is configured as owned")
    else:
        score += 50
        reasons.append("Third-party channel requires manual rights review")

    if candidate.license == "creativeCommon":
        reasons.append("YouTube metadata declares a Creative Commons license")
    else:
        score += 30
        reasons.append("No Creative Commons license is declared")

    if candidate.licensed_content:
        score += 20
        reasons.append("YouTube marks the video as containing licensed content")

    if candidate.category_id in HIGH_RISK_CATEGORIES:
        score += 20
        reasons.append("Music/movie category has elevated rights risk")

    if candidate.embeddable is False:
        score += 10
        reasons.append("Uploader disabled embedding")

    candidate.risk_score = min(score, 100)
    candidate.risk_reasons = reasons
    if candidate.channel_id in owned_channel_ids:
        candidate.rights_state = RightsState.APPROVED
    elif candidate.license == "creativeCommon" and score <= 50:
        candidate.rights_state = RightsState.NEEDS_REVIEW
    else:
        candidate.rights_state = RightsState.NEEDS_REVIEW
    logger.info(
        "Classified rights risk video_id=%s score=%d state=%s reason_count=%d",
        candidate.video_id,
        candidate.risk_score,
        candidate.rights_state,
        len(candidate.risk_reasons),
    )
    return candidate
