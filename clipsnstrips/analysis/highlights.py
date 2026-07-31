from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

from google import genai
from google.genai import types

from clipsnstrips.models import Segment, Transcript

SYSTEM_INSTRUCTION = """You are an expert short-form video editor.
Return only JSON with a top-level `segments` array. Each segment must contain:
start, end, hook, context, rationale, confidence, safety_notes, and visual_beats.
Each visual beat contains start, end, and description. Timestamps are seconds.
Choose self-contained, truthful spans with enough context. Avoid unsafe, private,
defamatory, sexually explicit, or misleading excerpts. Do not decide copyright or fair use."""


class GeminiHighlightAnalyzer:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def propose(
        self,
        transcript: Transcript,
        *,
        media: Path | None = None,
        min_seconds: float = 15,
        max_seconds: float = 60,
        max_segments: int = 5,
    ) -> list[Segment]:
        prompt = (
            f"Propose at most {max_segments} highlights, each between {min_seconds} and "
            f"{max_seconds} seconds.\n\nTRANSCRIPT:\n{transcript.text}"
        )
        contents: list[object] = [prompt]
        uploaded = None
        if media:
            uploaded = self.client.files.upload(file=str(media))
            contents.insert(0, uploaded)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            payload = json.loads(response.text or "{}")
        finally:
            if uploaded:
                with suppress(Exception):
                    self.client.files.delete(name=uploaded.name)
        segments = [Segment.model_validate(item) for item in payload.get("segments", [])]
        return validate_segments(
            segments,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
        )


def validate_segments(
    segments: list[Segment],
    *,
    min_seconds: float,
    max_seconds: float,
) -> list[Segment]:
    accepted: list[Segment] = []
    for segment in sorted(segments, key=lambda item: item.start):
        if segment.start < 0 or segment.end <= segment.start:
            continue
        if not min_seconds <= segment.duration <= max_seconds:
            continue
        if any(segment.start < prior.end and segment.end > prior.start for prior in accepted):
            continue
        accepted.append(segment)
    return accepted
