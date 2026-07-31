from __future__ import annotations

from pydantic import BaseModel

from clipsnstrips.models import Segment, VisualBeat


class PanelPrompt(BaseModel):
    index: int
    start: float
    end: float
    prompt: str


def panel_prompts(
    segment: Segment,
    *,
    style: str = "cinematic editorial comic, clean ink, rich color, no text",
) -> list[PanelPrompt]:
    beats = segment.visual_beats or [
        VisualBeat(
            start=segment.start,
            end=segment.end,
            description=segment.context or segment.hook,
        )
    ]
    continuity = (
        "Keep recurring characters, clothing, palette, lighting, and locations visually "
        "consistent across all panels. No logos, no captions, no speech bubbles, no watermarks."
    )
    return [
        PanelPrompt(
            index=index,
            start=beat.start,
            end=beat.end,
            prompt=(
                f"{style}. {continuity} Scene: {beat.description}. "
                f"Narrative context: {segment.context}. Vertical 2:3 composition."
            ),
        )
        for index, beat in enumerate(beats, start=1)
    ]
