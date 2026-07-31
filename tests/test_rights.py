from clipsnstrips.models import RightsState, VideoCandidate
from clipsnstrips.rights import classify_candidate


def candidate(**overrides) -> VideoCandidate:
    values = {
        "video_id": "video",
        "title": "Example",
        "channel_id": "third-party",
        "license": "youtube",
        "category_id": "10",
        "licensed_content": True,
    }
    values.update(overrides)
    return VideoCandidate(**values)


def test_owned_channel_is_approved() -> None:
    result = classify_candidate(
        candidate(channel_id="mine", category_id="22", licensed_content=False),
        {"mine"},
    )
    assert result.rights_state is RightsState.APPROVED
    assert result.risk_score == 30


def test_third_party_music_requires_review_with_reasons() -> None:
    result = classify_candidate(candidate())
    assert result.rights_state is RightsState.NEEDS_REVIEW
    assert result.risk_score == 100
    assert any("manual rights review" in reason for reason in result.risk_reasons)
