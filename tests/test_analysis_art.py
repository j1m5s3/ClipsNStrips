from clipsnstrips.analysis.highlights import (
    scaled_candidate_limit,
    validate_segments,
)
from clipsnstrips.art.prompts import panel_prompts, safe_panel_prompt
from clipsnstrips.models import Segment, Transcript, VisualBeat, Word


def segment(start: float, end: float) -> Segment:
    return Segment(
        start=start,
        end=end,
        hook="A useful hook",
        context="A consistent character explains an idea",
        confidence=0.8,
    )


def test_segment_validation_rejects_duration_and_overlap() -> None:
    result = validate_segments(
        [
            segment(0, 20),
            segment(10, 35),
            segment(40, 45),
            segment(50, 80),
            segment(90, 120),
        ],
        min_seconds=15,
        max_seconds=60,
        media_duration=100,
    )
    assert [(item.start, item.end) for item in result] == [(0, 20), (50, 80)]


def test_panel_prompts_preserve_timing_and_continuity() -> None:
    item = segment(10, 30)
    item.visual_beats = [
        VisualBeat(start=10, end=20, description="A person enters a workshop"),
        VisualBeat(start=20, end=30, description="The person demonstrates a tool"),
    ]
    transcript = Transcript(
        text="A person enters and demonstrates a tool",
        words=[
            Word(text="enters", start_ms=10_000, end_ms=11_000),
            Word(text="demonstrates", start_ms=20_000, end_ms=21_000),
        ],
    )
    prompts = panel_prompts(item, transcript=transcript, seconds_per_panel=8)
    assert len(prompts) == 3
    assert prompts[0].start == 10
    assert prompts[-1].end == 30
    assert all(prompt.end - prompt.start <= 8 for prompt in prompts)
    assert "consistent" in prompts[0].prompt
    assert "no watermarks" in prompts[1].prompt
    assert "enters" in prompts[0].prompt


def test_safe_panel_prompt_excludes_transcript_and_sensitive_action() -> None:
    item = segment(0, 8)
    item.visual_beats = [
        VisualBeat(
            start=0,
            end=8,
            description="An intimate sexual encounter in a bedroom",
        )
    ]
    transcript = Transcript(
        text="raw explicit dialogue",
        words=[Word(text="explicit-dialogue-token", start_ms=0, end_ms=1_000)],
    )
    prompt = panel_prompts(
        item,
        transcript=transcript,
        min_panels=1,
        seconds_per_panel=8,
    )[0]
    safe = safe_panel_prompt(prompt)
    assert "explicit-dialogue-token" in prompt.prompt
    assert "explicit-dialogue-token" not in safe.prompt
    assert "intimate sexual encounter" not in safe.prompt
    assert "non-explicit" in safe.prompt
    assert "fully covering clothing" in safe.prompt


def test_panel_count_scales_with_segment_duration() -> None:
    short = panel_prompts(segment(0, 24), seconds_per_panel=8)
    long = panel_prompts(segment(0, 53), seconds_per_panel=8)
    assert len(short) == 3
    assert len(long) == 7


def test_candidate_count_scales_with_source_duration() -> None:
    assert scaled_candidate_limit(60) == 3
    assert scaled_candidate_limit(386) == 4
    assert scaled_candidate_limit(1_800) == 12
