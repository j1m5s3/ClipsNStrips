from __future__ import annotations

import json
import logging
import math
import time
from contextlib import suppress
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from clipsnstrips.models import Segment, Transcript, VisualBeat

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """You are an expert short-form video editor.
Return only JSON with a top-level `segments` array. Each segment must contain:
start, end, hook, context, rationale, confidence, safety_notes, and visual_beats.
Each visual beat contains start, end, and description. Timestamps are seconds.
Choose self-contained, truthful spans with enough context. Avoid unsafe, private,
defamatory, sexually explicit, or misleading excerpts. Do not decide copyright or fair use."""


class HighlightProposal(BaseModel):
    start: float
    end: float
    hook: str
    context: str = ""
    rationale: str = ""
    confidence: float = Field(ge=0, le=1)
    safety_notes: list[str] = Field(default_factory=list)
    visual_beats: list[VisualBeat] = Field(default_factory=list)


class HighlightResponse(BaseModel):
    segments: list[HighlightProposal]


class GeminiHighlightAnalyzer:
    def __init__(self, api_key: str, model: str = "gemini-3.6-flash") -> None:
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
        media_duration: float | None = None,
    ) -> list[Segment]:
        logger.info(
            "Starting Gemini highlight analysis model=%s max_segments=%d",
            self.model,
            max_segments,
        )
        prompt = (
            f"Propose at most {max_segments} highlights, each between {min_seconds} and "
            f"{max_seconds} seconds."
            + (
                f" All timestamps must be within the source duration of "
                f"{media_duration:.3f} seconds."
                if media_duration is not None
                else ""
            )
            + f"\n\nTRANSCRIPT:\n{transcript.text}"
        )
        contents: list[object] = [prompt]
        uploaded = None
        if media:
            logger.info("Uploading media for Gemini analysis path=%s", media)
            uploaded = self.client.files.upload(file=str(media))
            uploaded = self._wait_until_active(uploaded.name)
            contents.insert(0, uploaded)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=HighlightResponse,
                    temperature=0.2,
                ),
            )
            parsed = response.parsed
            payload = (
                parsed
                if isinstance(parsed, HighlightResponse)
                else HighlightResponse.model_validate(json.loads(response.text or "{}"))
            )
        finally:
            if uploaded:
                with suppress(Exception):
                    self.client.files.delete(name=uploaded.name)
        segments = validate_segments(
            [Segment.model_validate(item.model_dump()) for item in payload.segments],
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            media_duration=media_duration,
        )
        logger.info("Completed Gemini highlight analysis candidate_count=%d", len(segments))
        return segments

    def _wait_until_active(self, name: str, timeout_seconds: float = 120) -> object:
        deadline = time.monotonic() + timeout_seconds
        while True:
            uploaded = self.client.files.get(name=name)
            state = getattr(uploaded.state, "name", str(uploaded.state)).upper()
            logger.debug("Gemini file state file=%s state=%s", name, state)
            if state == "ACTIVE":
                return uploaded
            if state == "FAILED":
                raise RuntimeError(f"Gemini file processing failed for {name}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Gemini file processing timed out for {name}")
            time.sleep(2)


def validate_segments(
    segments: list[Segment],
    *,
    min_seconds: float,
    max_seconds: float,
    media_duration: float | None = None,
) -> list[Segment]:
    accepted: list[Segment] = []
    for segment in sorted(segments, key=lambda item: item.start):
        if segment.start < 0 or segment.end <= segment.start:
            continue
        if not min_seconds <= segment.duration <= max_seconds:
            continue
        if media_duration is not None and segment.end > media_duration:
            continue
        if any(segment.start < prior.end and segment.end > prior.start for prior in accepted):
            continue
        accepted.append(segment)
    logger.debug(
        "Validated highlight segments proposed=%d accepted=%d",
        len(segments),
        len(accepted),
    )
    return accepted


def scaled_candidate_limit(
    media_duration: float | None,
    *,
    seconds_per_candidate: float = 120,
    min_candidates: int = 3,
    max_candidates: int = 12,
) -> int:
    if seconds_per_candidate <= 0:
        raise ValueError("seconds_per_candidate must be positive")
    if min_candidates < 1 or max_candidates < min_candidates:
        raise ValueError("candidate limits are invalid")
    if media_duration is None:
        return min_candidates
    return min(
        max(math.ceil(media_duration / seconds_per_candidate), min_candidates),
        max_candidates,
    )
