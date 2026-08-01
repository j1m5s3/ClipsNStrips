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


class SourceKind(StrEnum):
    VIDEO = "video"
    DOCUMENT = "document"


class ScriptMode(StrEnum):
    FAITHFUL = "faithful"
    ADAPTED = "adapted"


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
    bypassed: bool = False
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


class DocumentPage(BaseModel):
    page_number: int
    text: str
    start_char: int = 0
    end_char: int = 0
    extraction_method: str = "embedded"
    image_path: str | None = None
    image_checksum: str | None = None


class DocumentMetadata(BaseModel):
    title: str
    author: str = ""
    format: str
    page_count: int = 1
    word_count: int = 0


class ExtractedDocument(BaseModel):
    source_checksum: str
    extractor_key: str
    metadata: DocumentMetadata
    text: str
    pages: list[DocumentPage] = Field(default_factory=list)


class DocumentMetadataEvidence(BaseModel):
    value: str
    quote: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    confidence: float = Field(default=1, ge=0, le=1)


class DocumentContentSelection(BaseModel):
    requested_start_page: int = Field(ge=1)
    content_start_char: int = Field(ge=0)
    excluded_start_char: int = Field(default=0, ge=0)
    excluded_end_char: int = Field(ge=0)
    title: DocumentMetadataEvidence | None = None
    author: DocumentMetadataEvidence | None = None
    analyzer_key: str = "none"
    warnings: list[str] = Field(default_factory=list)


class DocumentSection(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    index: int
    title: str
    text: str
    start_char: int
    end_char: int
    page_start: int = 1
    page_end: int = 1
    summary: str = ""


class StoryCharacter(BaseModel):
    id: str
    name: str
    visual_description: str
    clothing: str = ""
    role: str = ""
    relationships: list[str] = Field(default_factory=list)
    voice_description: str = ""
    uncertainty: list[str] = Field(default_factory=list)


class StoryLocation(BaseModel):
    id: str
    name: str
    description: str


class StoryBible(BaseModel):
    summary: str
    era: str = ""
    palette: str = ""
    characters: list[StoryCharacter] = Field(default_factory=list)
    locations: list[StoryLocation] = Field(default_factory=list)


class VoiceAssignment(BaseModel):
    character_id: str
    voice_id: str
    voice_name: str = ""
    style: str = ""


class VoiceBible(BaseModel):
    provider: str
    model: str
    assignments: list[VoiceAssignment] = Field(default_factory=list)

    def voice_for(self, character_id: str) -> VoiceAssignment:
        assignment = next(
            (item for item in self.assignments if item.character_id == character_id),
            None,
        )
        if assignment is None:
            assignment = next(
                (item for item in self.assignments if item.character_id == "narrator"),
                None,
            )
        if assignment is None:
            raise LookupError(f"No voice assignment for {character_id}")
        return assignment


class NarrationLine(BaseModel):
    index: int
    section_id: str
    character_id: str = "narrator"
    text: str
    source_start: int
    source_end: int
    adapted: bool = False
    scene_description: str = ""


class NarrationScript(BaseModel):
    mode: ScriptMode
    source_checksum: str
    lines: list[NarrationLine] = Field(default_factory=list)


class NarrationClip(BaseModel):
    line_index: int
    section_id: str
    character_id: str
    path: str
    duration_ms: int
    checksum: str
    provider_id: str | None = None
    alignment: list[Word] = Field(default_factory=list)


class NarrationManifest(BaseModel):
    provider_key: str
    script_checksum: str
    voice_bible_checksum: str
    clips: list[NarrationClip] = Field(default_factory=list)
    total_duration_ms: int = 0


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
    source_kind: SourceKind = SourceKind.VIDEO
    source: VideoCandidate | None = None
    source_path: str | None = None
    source_checksum: str | None = None
    rights_state: RightsState = RightsState.NEEDS_REVIEW
    rights_evidence: list[RightsEvidence] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    transcript_path: str | None = None
    document_metadata: DocumentMetadata | None = None
    extracted_document_path: str | None = None
    content_selection_path: str | None = None
    sections_path: str | None = None
    script_path: str | None = None
    story_bible_path: str | None = None
    voice_bible_path: str | None = None
    narration_manifest_path: str | None = None
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
                and (
                    (artifact.segment_id is not None and existing.segment_id == artifact.segment_id)
                    or (artifact.segment_id is None and existing.path == artifact.path)
                )
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
