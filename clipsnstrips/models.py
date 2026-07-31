from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class RightsState(StrEnum):
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"


class Stage(StrEnum):
    DISCOVERED = "discovered"
    INGEST_AUTHORIZED = "ingest_authorized"
    INGESTED = "ingested"
    ANALYZED = "analyzed"
    SPANS_REVIEWED = "spans_reviewed"
    RENDERED = "rendered"
    OUTPUT_REVIEWED = "output_reviewed"
    UPLOADED_PRIVATE = "uploaded_private"
    PUBLISHED = "published"


class RightsEvidence(BaseModel):
    kind: str
    value: str
    source: str | None = None
    recorded_at: datetime = Field(default_factory=utc_now)


class Approval(BaseModel):
    purpose: str
    approved: bool
    reviewer: str
    notes: str
    created_at: datetime = Field(default_factory=utc_now)


class VideoCandidate(BaseModel):
    video_id: str
    title: str
    channel_id: str
    channel_title: str = ""
    description: str = ""
    duration: str | None = None
    category_id: str | None = None
    license: str | None = None
    embeddable: bool | None = None
    licensed_content: bool | None = None
    view_count: int = 0
    like_count: int = 0
    published_at: datetime | None = None
    risk_score: int = 100
    risk_reasons: list[str] = Field(default_factory=list)
    rights_state: RightsState = RightsState.NEEDS_REVIEW


class Word(BaseModel):
    text: str
    start_ms: int
    end_ms: int
    speaker: str | None = None


class Transcript(BaseModel):
    text: str
    words: list[Word] = Field(default_factory=list)
    provider_id: str | None = None


class VisualBeat(BaseModel):
    start: float
    end: float
    description: str


class Segment(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    start: float
    end: float
    hook: str
    context: str = ""
    rationale: str = ""
    confidence: float = Field(ge=0, le=1)
    safety_notes: list[str] = Field(default_factory=list)
    visual_beats: list[VisualBeat] = Field(default_factory=list)
    approved: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start


class Artifact(BaseModel):
    kind: str
    path: str
    segment_id: str | None = None
    checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UploadRecord(BaseModel):
    video_id: str
    artifact_path: str
    privacy_status: str
    uploaded_at: datetime = Field(default_factory=utc_now)


class JobManifest(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    stage: Stage = Stage.DISCOVERED
    source: VideoCandidate | None = None
    source_path: str | None = None
    source_checksum: str | None = None
    rights_state: RightsState = RightsState.NEEDS_REVIEW
    rights_evidence: list[RightsEvidence] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    transcript_path: str | None = None
    segments: list[Segment] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    uploads: list[UploadRecord] = Field(default_factory=list)

    def approval_for(self, purpose: str) -> Approval | None:
        return next(
            (item for item in reversed(self.approvals) if item.purpose == purpose),
            None,
        )

    def require_approval(self, purpose: str) -> None:
        approval = self.approval_for(purpose)
        if not approval or not approval.approved:
            raise PermissionError(f"Job requires an explicit '{purpose}' approval")

    def add_artifact(self, artifact: Artifact) -> None:
        self.artifacts = [
            existing
            for existing in self.artifacts
            if not (
                existing.kind == artifact.kind
                and existing.path == artifact.path
                and existing.segment_id == artifact.segment_id
            )
        ]
        self.artifacts.append(artifact)

    @property
    def directory_name(self) -> str:
        return self.id


class RenderOptions(BaseModel):
    vertical: bool = False
    captions: bool = False
    width: int = 1080
    height: int = 1920
    audio_lufs: float = -16.0


def artifact_path(root: Path, artifact: Artifact) -> Path:
    return root / artifact.path
