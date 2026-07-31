from clipsnstrips.analysis.highlights import validate_segments
from clipsnstrips.art.prompts import panel_prompts
from clipsnstrips.models import Segment, VisualBeat


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
    prompts = panel_prompts(item)
    assert len(prompts) == 2
    assert prompts[0].start == 10
    assert "consistent" in prompts[0].prompt
    assert "no watermarks" in prompts[1].prompt
