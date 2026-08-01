from __future__ import annotations

import math

from pydantic import BaseModel, Field

from clipsnstrips.analysis.scene_context import SegmentSceneContext
from clipsnstrips.models import Segment, Transcript, VisualBeat


class PanelPrompt(BaseModel):
    index: int
    start: float
    end: float
    prompt: str
    safe_prompt: str = ""
    subject_ids: list[str] = Field(default_factory=list)
    reference_indices: list[int] = Field(default_factory=list)


def panel_prompts(
    segment: Segment,
    *,
    transcript: Transcript | None = None,
    scene_context: SegmentSceneContext | None = None,
    seconds_per_panel: float = 8,
    min_panels: int = 3,
    max_panels: int = 12,
    style: str = "cinematic editorial comic, clean ink, rich color, no text",
) -> list[PanelPrompt]:
    if seconds_per_panel <= 0:
        raise ValueError("seconds_per_panel must be positive")
    if min_panels < 1 or max_panels < min_panels:
        raise ValueError("panel limits are invalid")
    beats = segment.visual_beats or [
        VisualBeat(
            start=segment.start,
            end=segment.end,
            description=segment.context or segment.hook,
        )
    ]
    panel_count = min(
        max(math.ceil(segment.duration / seconds_per_panel), min_panels),
        max_panels,
    )
    panel_duration = segment.duration / panel_count
    continuity = (
        "Keep recurring characters, clothing, palette, lighting, and locations visually "
        "consistent across all panels. No logos, no captions, no speech bubbles, no watermarks."
    )
    prompts: list[PanelPrompt] = []
    for index in range(1, panel_count + 1):
        start = segment.start + (index - 1) * panel_duration
        end = segment.end if index == panel_count else start + panel_duration
        beat = max(
            beats,
            key=lambda item: max(0, min(end, item.end) - max(start, item.start)),
        )
        excerpt = _transcript_excerpt(transcript, start, end)
        spoken_context = f" Spoken content for this moment: {excerpt}." if excerpt else ""
        panel_context = scene_context.panel(index) if scene_context else None
        subject_ids = panel_context.subject_ids if panel_context else []
        subjects = (
            [
                subject
                for subject in scene_context.subjects
                if panel_context is None or subject.id in subject_ids
            ]
            if scene_context
            else []
        )
        subject_bible = " ".join(
            (
                f"{subject.id}: {subject.visual_description}; clothing: {subject.clothing}; "
                f"role: {subject.role}."
            )
            for subject in subjects
        )
        visual_context = (
            (
                f" Verified setting: {panel_context.setting}. "
                f"Verified action: {panel_context.action}. "
                f"Composition: {panel_context.composition}."
            )
            if panel_context
            else ""
        )
        identity_context = (
            f" Preserve these exact segment-scoped subjects: {subject_bible}"
            if subject_bible
            else ""
        )
        safe_subjects = " ".join(
            (
                f"{subject.id}: "
                f"{_safe_description(subject.visual_description, 'an adult figure')}; "
                f"clothing: "
                f"{_safe_description(subject.clothing, 'ordinary fully covering clothing')}."
            )
            for subject in subjects
        )
        safe_setting = _safe_description(
            panel_context.setting if panel_context else "",
            "a neutral public setting",
        )
        safe_composition = _safe_description(
            panel_context.composition if panel_context else "",
            "a balanced editorial composition",
        )
        safe_style = _safe_description(style, "clean editorial comic")
        prompts.append(
            PanelPrompt(
                index=index,
                start=start,
                end=end,
                subject_ids=subject_ids,
                reference_indices=panel_context.reference_indices if panel_context else [],
                safe_prompt=(
                    f"{safe_style}. Create a benign editorial comic illustration. "
                    "The scene must be non-explicit and non-graphic. Depict only adults in "
                    "ordinary, fully covering clothing. If a person's age or safe depiction "
                    "is uncertain, omit that person and use neutral symbolic objects. "
                    f"Setting: {safe_setting}. Composition: {safe_composition}. "
                    f"Non-sensitive subject continuity: {safe_subjects or 'none required'}. "
                    "No intimacy, nudity, sexualized posing, violence, injury, logos, "
                    "captions, speech bubbles, or watermarks. Vertical 2:3 composition."
                ),
                prompt=(
                    f"{style}. {continuity} Panel {index} of {panel_count}; depict a distinct "
                    f"moment in sequence. Scene guidance: {beat.description}. "
                    f"Narrative context: {segment.context}.{visual_context}"
                    f"{identity_context}{spoken_context} "
                    "Vertical 2:3 composition."
                ),
            )
        )
    return prompts


def safe_panel_prompt(prompt: PanelPrompt) -> PanelPrompt:
    if not prompt.safe_prompt:
        raise ValueError("Panel prompt has no safe fallback")
    return prompt.model_copy(update={"prompt": prompt.safe_prompt})


_SENSITIVE_DESCRIPTION_TERMS = {
    "bed",
    "breast",
    "child",
    "explicit",
    "genital",
    "intimate",
    "lingerie",
    "minor",
    "naked",
    "nude",
    "seductive",
    "sexual",
    "teen",
    "underwear",
    "young",
}


def _safe_description(value: str, fallback: str) -> str:
    normalized = value.casefold()
    if not value.strip() or any(term in normalized for term in _SENSITIVE_DESCRIPTION_TERMS):
        return fallback
    return value[:200].strip()


def _transcript_excerpt(
    transcript: Transcript | None,
    start: float,
    end: float,
) -> str:
    if transcript is None:
        return ""
    words = [
        word.text
        for word in transcript.words
        if word.end_ms > start * 1000 and word.start_ms < end * 1000
    ]
    excerpt = " ".join(words)
    return excerpt[:500].strip()
