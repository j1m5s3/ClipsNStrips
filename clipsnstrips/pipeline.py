from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from clipsnstrips.analysis.document_content import (
    FrontMatterAnalyzer,
    select_document_content,
)
from clipsnstrips.analysis.document_sections import (
    GeminiDocumentScriptAnalyzer,
    split_document_sections,
)
from clipsnstrips.analysis.highlights import (
    GeminiHighlightAnalyzer,
    scaled_candidate_limit,
)
from clipsnstrips.analysis.scene_context import (
    FrameReference,
    GeminiSceneAnalyzer,
    GeminiStoryAnalyzer,
    SceneContextArtifact,
    SegmentSceneContext,
)
from clipsnstrips.analysis.transcription import AssemblyAITranscriber
from clipsnstrips.art.prompts import (
    ComicPagePrompt,
    PanelPrompt,
    comic_page_prompts,
    panel_prompts,
    safe_panel_prompt,
)
from clipsnstrips.art.providers import ImageModerationBlocked, ImageProvider
from clipsnstrips.ingest.documents import GeminiDocumentOCR, extract_document
from clipsnstrips.jobs import JobStore, sha256_file
from clipsnstrips.media.ffmpeg import FFmpeg
from clipsnstrips.models import (
    Artifact,
    DocumentContentSelection,
    DocumentSection,
    ExtractedDocument,
    NarrationClip,
    NarrationManifest,
    NarrationScript,
    RenderOptions,
    ScriptMode,
    Segment,
    SourceKind,
    Stage,
    StoryBible,
    Transcript,
    VisualBeat,
    Word,
)
from clipsnstrips.narration.providers import NarrationProvider
from clipsnstrips.narration.script import build_narration_script
from clipsnstrips.narration.voices import build_voice_bible

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, store: JobStore, ffmpeg: FFmpeg) -> None:
        self.store = store
        self.ffmpeg = ffmpeg

    def analyze(
        self,
        job_id: str,
        transcriber: AssemblyAITranscriber,
        analyzer: GeminiHighlightAnalyzer,
        *,
        min_seconds: float,
        max_seconds: float,
        seconds_per_candidate: float = 120,
        min_candidates: int = 3,
        max_candidates: int = 12,
    ) -> None:
        logger.info("Starting analysis job_id=%s", job_id)
        manifest = self.store.load(job_id)
        manifest.require_approval("ingest")
        source = self._source(manifest)
        probe = self.ffmpeg.probe(source)
        raw_duration = probe.get("format", {}).get("duration")
        media_duration = float(raw_duration) if raw_duration is not None else None
        candidate_limit = scaled_candidate_limit(
            media_duration,
            seconds_per_candidate=seconds_per_candidate,
            min_candidates=min_candidates,
            max_candidates=max_candidates,
        )
        analysis_dir = self.store.directory(job_id) / "analysis"
        audio = analysis_dir / "audio.wav"
        transcript_path = analysis_dir / "transcript.json"
        if transcript_path.exists():
            logger.info("Reusing transcript job_id=%s path=%s", job_id, transcript_path)
            transcript = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))
        else:
            self.ffmpeg.extract_audio(source, audio)
            transcript = transcriber.transcribe(audio)
            transcript_path = self.store.write_json(
                job_id,
                "analysis/transcript.json",
                transcript.model_dump(mode="json"),
            )
        manifest.transcript_path = transcript_path.relative_to(
            self.store.directory(job_id)
        ).as_posix()
        manifest.add_artifact(
            Artifact(
                kind="transcript",
                path=manifest.transcript_path,
                checksum=sha256_file(transcript_path),
            )
        )
        self.store.save(manifest)
        segments = analyzer.propose(
            transcript,
            media=source,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            max_segments=candidate_limit,
            media_duration=media_duration,
        )
        segments_path = self.store.write_json(
            job_id,
            "analysis/candidate_segments.json",
            [segment.model_dump(mode="json") for segment in segments],
        )
        manifest.segments = segments
        manifest.add_artifact(
            Artifact(
                kind="candidate_segments",
                path=segments_path.relative_to(self.store.directory(job_id)).as_posix(),
                checksum=sha256_file(segments_path),
            )
        )
        manifest.stage = Stage.ANALYZED
        self.store.save(manifest)
        logger.info(
            "Completed analysis job_id=%s candidate_count=%d",
            job_id,
            len(segments),
        )

    def analyze_document(
        self,
        job_id: str,
        *,
        ocr: GeminiDocumentOCR | None,
        script_analyzer: GeminiDocumentScriptAnalyzer | None,
        story_analyzer: GeminiStoryAnalyzer,
        mode: ScriptMode,
        front_matter_analyzer: FrontMatterAnalyzer | None = None,
        content_start_page: int | None = None,
        front_matter_max_chars: int = 20_000,
        target_section_words: int = 900,
        max_section_words: int = 1_400,
        max_pages: int = 500,
        embedded_text_min_chars: int = 40,
    ) -> dict[str, Any]:
        manifest = self.store.load(job_id)
        manifest.require_approval("ingest")
        if manifest.source_kind != SourceKind.DOCUMENT:
            raise ValueError("Document analysis requires a document job")
        document = extract_document(
            self.store,
            manifest,
            ocr,
            max_pages=max_pages,
            embedded_text_min_chars=embedded_text_min_chars,
        )
        selection: DocumentContentSelection | None = None
        selection_path: Path | None = None
        if content_start_page is not None:
            selection_signature = {
                "source_checksum": document.source_checksum,
                "content_start_page": content_start_page,
                "analyzer": (front_matter_analyzer.cache_key if front_matter_analyzer else "none"),
                "max_front_matter_chars": front_matter_max_chars,
            }
            selection_path = self.store.directory(job_id) / "analysis" / "content-selection.json"
            selection_state_path = (
                self.store.directory(job_id) / "analysis" / "content-selection-generation.json"
            )
            selection_state = self._generation_state(
                selection_state_path,
                selection_signature,
            )
            if selection_path.exists() and selection_state.get("completed", {}).get(
                "selection"
            ) == sha256_file(selection_path):
                selection = DocumentContentSelection.model_validate_json(
                    selection_path.read_text(encoding="utf-8")
                )
            else:
                selection = select_document_content(
                    document,
                    content_start_page,
                    front_matter_analyzer,
                    max_front_matter_chars=front_matter_max_chars,
                )
                self.store.write_json(
                    job_id,
                    "analysis/content-selection.json",
                    selection.model_dump(mode="json"),
                )
                selection_state["completed"] = {"selection": sha256_file(selection_path)}
                selection_state_path.write_text(
                    json.dumps(selection_state, indent=2),
                    encoding="utf-8",
                )
        sections = split_document_sections(
            document,
            target_words=target_section_words,
            max_words=max_section_words,
            content_start_char=selection.content_start_char if selection else 0,
        )
        sections_path = self.store.write_json(
            job_id,
            "analysis/sections.json",
            [section.model_dump(mode="json") for section in sections],
        )
        script_signature = {
            "source_checksum": document.source_checksum,
            "mode": mode,
            "sections": [section.model_dump(mode="json") for section in sections],
            "analyzer": script_analyzer.cache_key if script_analyzer else "faithful-v1",
            "content_selection": selection.model_dump(mode="json") if selection else None,
        }
        script_path = self.store.directory(job_id) / "analysis" / "script.json"
        script_state_path = self.store.directory(job_id) / "analysis" / "script-generation.json"
        script_state = self._generation_state(script_state_path, script_signature)
        if script_path.exists() and script_state.get("completed", {}).get("script") == sha256_file(
            script_path
        ):
            script = NarrationScript.model_validate_json(script_path.read_text(encoding="utf-8"))
        else:
            script = build_narration_script(
                document,
                sections,
                mode,
                analyzer=script_analyzer,
                selection=selection,
            )
            self.store.write_json(
                job_id,
                "analysis/script.json",
                script.model_dump(mode="json"),
            )
            script_state["completed"] = {"script": sha256_file(script_path)}
            script_state_path.write_text(json.dumps(script_state, indent=2), encoding="utf-8")

        story_signature = {
            "source_checksum": document.source_checksum,
            "script_checksum": sha256_file(script_path),
            "analyzer": story_analyzer.cache_key,
            "content_selection_checksum": (sha256_file(selection_path) if selection_path else None),
        }
        story_path = self.store.directory(job_id) / "analysis" / "story-bible.json"
        story_state_path = self.store.directory(job_id) / "analysis" / "story-generation.json"
        story_state = self._generation_state(story_state_path, story_signature)
        if story_path.exists() and story_state.get("completed", {}).get("story") == sha256_file(
            story_path
        ):
            story = StoryBible.model_validate_json(story_path.read_text(encoding="utf-8"))
        else:
            story = story_analyzer.analyze_story(sections, script)
            self.store.write_json(
                job_id,
                "analysis/story-bible.json",
                story.model_dump(mode="json"),
            )
            story_state["completed"] = {"story": sha256_file(story_path)}
            story_state_path.write_text(json.dumps(story_state, indent=2), encoding="utf-8")

        manifest = self.store.load(job_id)
        manifest.content_selection_path = (
            selection_path.relative_to(self.store.directory(job_id)).as_posix()
            if selection_path
            else None
        )
        if selection_path is None:
            manifest.artifacts = [
                artifact
                for artifact in manifest.artifacts
                if artifact.kind != "document_content_selection"
            ]
        manifest.sections_path = sections_path.relative_to(self.store.directory(job_id)).as_posix()
        manifest.script_path = script_path.relative_to(self.store.directory(job_id)).as_posix()
        manifest.story_bible_path = story_path.relative_to(self.store.directory(job_id)).as_posix()
        manifest.segments = [
            Segment(
                id=section.id,
                start=float(index),
                end=float(index + 1),
                hook=section.title,
                context=section.summary,
                confidence=1,
            )
            for index, section in enumerate(sections)
        ]
        for kind, path in (
            *((("document_content_selection", selection_path),) if selection_path else ()),
            ("document_sections", sections_path),
            ("narration_script", script_path),
            ("story_bible", story_path),
        ):
            manifest.add_artifact(
                Artifact(
                    kind=kind,
                    path=path.relative_to(self.store.directory(job_id)).as_posix(),
                    checksum=sha256_file(path),
                )
            )
        manifest.stage = Stage.ANALYZED
        self.store.save(manifest)
        return self.document_preflight(job_id)

    def synthesize_document_narration(
        self,
        job_id: str,
        provider: NarrationProvider,
        *,
        voice_ids: list[str],
        pause_seconds: float = 0.25,
        audio_lufs: float = -16,
    ) -> NarrationManifest:
        manifest = self.store.load(job_id)
        manifest.require_approval("spans")
        script = self._load_document_model(manifest, "script_path", NarrationScript)
        story = self._load_document_model(manifest, "story_bible_path", StoryBible)
        sections = self._document_sections(manifest)
        selected_section_ids = {segment.id for segment in manifest.segments if segment.approved}
        if not selected_section_ids:
            raise ValueError("Approve at least one document section before narration")
        sections = [section for section in sections if section.id in selected_section_ids]
        script = script.model_copy(
            update={
                "lines": [line for line in script.lines if line.section_id in selected_section_ids]
            }
        )
        if not script.lines:
            raise ValueError("Selected document sections contain no narration lines")
        voice_bible = build_voice_bible(
            story,
            voice_ids,
            model=getattr(provider, "model", provider.cache_key),
        )
        voice_path = self.store.write_json(
            job_id,
            "analysis/voice-bible.json",
            voice_bible.model_dump(mode="json"),
        )
        script_path = self.store.directory(job_id) / (manifest.script_path or "")
        signature = {
            "provider": provider.cache_key,
            "script_checksum": sha256_file(script_path),
            "voice_bible": voice_bible.model_dump(mode="json"),
            "selected_sections": sorted(selected_section_ids),
            "pause_seconds": pause_seconds,
            "audio_lufs": audio_lufs,
        }
        state_path = self.store.directory(job_id) / "narration" / "generation.json"
        state = self._generation_state(state_path, signature)
        clips: list[NarrationClip] = []
        for offset, line in enumerate(script.lines):
            destination = (
                self.store.directory(job_id)
                / "narration"
                / "lines"
                / (f"line-{line.index:05d}{getattr(provider, 'file_extension', '.wav')}")
            )
            completed = state["completed"].get(str(line.index))
            if (
                completed
                and destination.exists()
                and completed.get("checksum") == sha256_file(destination)
            ):
                clip = NarrationClip.model_validate(completed["clip"])
            else:
                voice = voice_bible.voice_for(line.character_id)
                result = provider.synthesize(
                    line,
                    destination,
                    voice=voice,
                    previous_text=script.lines[offset - 1].text
                    if offset and script.lines[offset - 1].character_id == line.character_id
                    else None,
                    next_text=script.lines[offset + 1].text
                    if offset + 1 < len(script.lines)
                    and script.lines[offset + 1].character_id == line.character_id
                    else None,
                )
                duration_ms = result.duration_ms or round(self.ffmpeg.duration(destination) * 1000)
                alignment = result.alignment or self._approximate_alignment(
                    line.text,
                    duration_ms,
                    line.character_id,
                )
                for word in alignment:
                    word.speaker = line.character_id
                clip = NarrationClip(
                    line_index=line.index,
                    section_id=line.section_id,
                    character_id=line.character_id,
                    path=destination.relative_to(self.store.directory(job_id)).as_posix(),
                    duration_ms=duration_ms,
                    checksum=sha256_file(destination),
                    provider_id=result.provider_id,
                    alignment=alignment,
                )
                state["completed"][str(line.index)] = {
                    "checksum": clip.checksum,
                    "clip": clip.model_dump(mode="json"),
                }
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            clips.append(clip)

        section_tracks: list[Path] = []
        segments: list[Segment] = []
        transcript_words: list[Word] = []
        timeline_ms = 0
        line_by_index = {line.index: line for line in script.lines}
        for section in sections:
            section_clips = [clip for clip in clips if clip.section_id == section.id]
            if not section_clips:
                continue
            track = self.store.directory(job_id) / "narration" / "sections" / f"{section.id}.wav"
            self.ffmpeg.concat_audio(
                [self.store.directory(job_id) / clip.path for clip in section_clips],
                track,
                pause_seconds=pause_seconds,
                audio_lufs=audio_lufs,
            )
            section_tracks.append(track)
            if segments:
                timeline_ms += round(pause_seconds * 1000)
            section_start_ms = timeline_ms
            local_ms = 0
            beats: list[VisualBeat] = []
            for clip in section_clips:
                line = line_by_index[clip.line_index]
                line_start_ms = section_start_ms + local_ms
                for word in clip.alignment:
                    transcript_words.append(
                        word.model_copy(
                            update={
                                "start_ms": word.start_ms + line_start_ms,
                                "end_ms": word.end_ms + line_start_ms,
                            }
                        )
                    )
                beats.append(
                    VisualBeat(
                        start=line_start_ms / 1000,
                        end=(line_start_ms + clip.duration_ms) / 1000,
                        description=line.scene_description or section.summary,
                    )
                )
                local_ms += clip.duration_ms + round(pause_seconds * 1000)
            section_duration_ms = round(self.ffmpeg.duration(track) * 1000)
            timeline_ms += section_duration_ms
            segments.append(
                Segment(
                    id=section.id,
                    start=section_start_ms / 1000,
                    end=timeline_ms / 1000,
                    hook=section.title,
                    context=section.summary,
                    confidence=1,
                    visual_beats=beats,
                    approved=True,
                )
            )

        full_track = self.store.directory(job_id) / "narration" / "full-track.wav"
        self.ffmpeg.concat_audio(
            section_tracks,
            full_track,
            pause_seconds=pause_seconds,
            audio_lufs=audio_lufs,
        )
        transcript = Transcript(
            text=" ".join(line.text for line in script.lines),
            words=transcript_words,
            provider_id=provider.cache_key,
        )
        transcript_path = self.store.write_json(
            job_id,
            "analysis/transcript.json",
            transcript.model_dump(mode="json"),
        )
        narration = NarrationManifest(
            provider_key=provider.cache_key,
            script_checksum=sha256_file(script_path),
            voice_bible_checksum=sha256_file(voice_path),
            clips=clips,
            total_duration_ms=round(self.ffmpeg.duration(full_track) * 1000),
        )
        narration_path = self.store.write_json(
            job_id,
            "narration/narration.json",
            narration.model_dump(mode="json"),
        )
        manifest.voice_bible_path = voice_path.relative_to(self.store.directory(job_id)).as_posix()
        manifest.narration_manifest_path = narration_path.relative_to(
            self.store.directory(job_id)
        ).as_posix()
        manifest.transcript_path = transcript_path.relative_to(
            self.store.directory(job_id)
        ).as_posix()
        manifest.segments = segments
        for kind, path in (
            ("voice_bible", voice_path),
            ("narration_manifest", narration_path),
            ("narration_audio", full_track),
            ("transcript", transcript_path),
        ):
            manifest.add_artifact(
                Artifact(
                    kind=kind,
                    path=path.relative_to(self.store.directory(job_id)).as_posix(),
                    checksum=sha256_file(path),
                )
            )
        manifest.stage = Stage.SPANS_REVIEWED
        self.store.save(manifest)
        return narration

    def render_clips(
        self,
        job_id: str,
        options: RenderOptions,
    ) -> list[Path]:
        logger.info("Starting clip rendering job_id=%s vertical=%s", job_id, options.vertical)
        manifest = self.store.load(job_id)
        manifest.require_approval("spans")
        source = self._source(manifest)
        outputs: list[Path] = []
        for segment in manifest.segments:
            if not segment.approved:
                continue
            destination = self.store.directory(job_id) / "clips" / f"{segment.id}.mp4"
            self.ffmpeg.render_clip(source, destination, segment, options)
            relative = destination.relative_to(self.store.directory(job_id)).as_posix()
            manifest.add_artifact(
                Artifact(
                    kind="clip",
                    path=relative,
                    segment_id=segment.id,
                    checksum=sha256_file(destination),
                )
            )
            outputs.append(destination)
            logger.info(
                "Rendered clip job_id=%s segment_id=%s path=%s",
                job_id,
                segment.id,
                destination,
            )
        manifest.stage = Stage.RENDERED
        self.store.save(manifest)
        logger.info("Completed clip rendering job_id=%s output_count=%d", job_id, len(outputs))
        return outputs

    def render_document(
        self,
        job_id: str,
        provider: ImageProvider,
        story_analyzer: GeminiStoryAnalyzer,
        *,
        style: str,
        words_per_panel: int = 90,
        min_panels: int = 1,
        max_panels: int = 24,
        subpanels_per_image: int = 4,
        moderation_fallback_enabled: bool = True,
        moderation_final_action: str = "placeholder",
    ) -> Path:
        if words_per_panel < 1 or min_panels < 1 or max_panels < min_panels:
            raise ValueError("Invalid document panel limits")
        if not 1 <= subpanels_per_image <= 4:
            raise ValueError("subpanels_per_image must be between 1 and 4")
        manifest = self.store.load(job_id)
        manifest.require_approval("spans")
        if manifest.source_kind != SourceKind.DOCUMENT:
            raise ValueError("Document rendering requires a document job")
        sections = self._document_sections(manifest)
        script = self._load_document_model(manifest, "script_path", NarrationScript)
        story = self._load_document_model(manifest, "story_bible_path", StoryBible)
        transcript = self.transcript(job_id)
        character_references = self._story_reference_images(
            job_id,
            story,
            provider,
            style=style,
            moderation_fallback_enabled=moderation_fallback_enabled,
            moderation_final_action=moderation_final_action,
        )
        lines_by_section = {
            section.id: [line for line in script.lines if line.section_id == section.id]
            for section in sections
        }
        segment_by_id = {segment.id: segment for segment in manifest.segments}
        section_videos: list[Path] = []
        for section in sections:
            segment = segment_by_id.get(section.id)
            if segment is None:
                continue
            narration_track = (
                self.store.directory(job_id) / "narration" / "sections" / f"{section.id}.wav"
            )
            if not narration_track.exists():
                raise FileNotFoundError(narration_track)
            panel_count = min(
                max(math.ceil(len(section.text.split()) / words_per_panel), min_panels),
                max_panels,
            )
            context_signature = {
                "analyzer": story_analyzer.cache_key,
                "story": story.model_dump(mode="json"),
                "section": section.model_dump(mode="json"),
                "lines": [line.model_dump(mode="json") for line in lines_by_section[section.id]],
                "panel_count": panel_count,
            }
            segment_dir = self.store.directory(job_id) / "art" / section.id
            context_path = segment_dir / "scene-context.json"
            context_state_path = segment_dir / "scene-context-generation.json"
            context_state = self._generation_state(
                context_state_path,
                context_signature,
            )
            if context_path.exists() and context_state.get("completed", {}).get(
                "checksum"
            ) == sha256_file(context_path):
                context = SegmentSceneContext.model_validate_json(
                    context_path.read_text(encoding="utf-8")
                )
            else:
                context = story_analyzer.section_context(
                    story,
                    section,
                    lines_by_section[section.id],
                    panel_count=panel_count,
                )
                context_path.parent.mkdir(parents=True, exist_ok=True)
                context_path.write_text(context.model_dump_json(indent=2), encoding="utf-8")
                context_state["completed"] = {"checksum": sha256_file(context_path)}
                context_state_path.write_text(
                    json.dumps(context_state, indent=2),
                    encoding="utf-8",
                )
            prompts = panel_prompts(
                segment,
                transcript=transcript,
                scene_context=context,
                seconds_per_panel=max(segment.duration, 0.1),
                min_panels=panel_count,
                max_panels=panel_count,
                style=style,
            )
            generation_prompts: list[PanelPrompt] = (
                comic_page_prompts(prompts, max_subpanels=subpanels_per_image)
                if subpanels_per_image > 1
                else prompts
            )
            prompt_values = [prompt.model_dump(mode="json") for prompt in generation_prompts]
            self.store.write_json(
                job_id,
                f"art/{section.id}/prompts.json",
                prompt_values,
            )
            generation_signature = {
                "source_checksum": manifest.source_checksum,
                "story_bible": story.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
                "provider": provider.cache_key,
                "prompts": prompt_values,
                "layout": {
                    "version": "composite-v1",
                    "subpanels_per_image": subpanels_per_image,
                },
                "moderation_policy": {
                    "version": "compliant-v1",
                    "enabled": moderation_fallback_enabled,
                    "final_action": moderation_final_action,
                },
            }
            state_path = segment_dir / "generation.json"
            state = self._generation_state(state_path, generation_signature)
            images: list[Path] = []
            metadata: list[dict[str, Any]] = []
            previous_page: Path | None = None
            for prompt in generation_prompts:
                is_composite = isinstance(prompt, ComicPagePrompt)
                destination = segment_dir / (
                    f"page-{prompt.index:03d}.png"
                    if is_composite
                    else f"panel-{prompt.index:03d}.png"
                )
                completed = state["completed"].get(str(prompt.index))
                if (
                    completed
                    and destination.exists()
                    and completed.get("checksum") == sha256_file(destination)
                ):
                    panel_metadata = completed["metadata"]
                else:
                    references = [
                        character_references[subject_id]
                        for subject_id in prompt.subject_ids
                        if subject_id in character_references
                    ]
                    location_ids = (
                        prompt.location_ids
                        if isinstance(prompt, ComicPagePrompt)
                        else [prompt.location_id]
                        if prompt.location_id
                        else []
                    )
                    references.extend(
                        character_references[location_id]
                        for location_id in location_ids
                        if location_id in character_references
                    )
                    panel_metadata = self._generate_panel_with_fallback(
                        provider,
                        prompt,
                        destination,
                        list(dict.fromkeys(references))[:15],
                        previous_page,
                        enabled=moderation_fallback_enabled,
                        final_action=moderation_final_action,
                    )
                if is_composite:
                    cell_paths = self._comic_page_cells(
                        destination,
                        segment_dir,
                        prompt,
                        completed,
                    )
                    images.extend(cell_paths)
                else:
                    cell_paths = [destination]
                    images.append(destination)
                page_metadata = {
                    **panel_metadata,
                    "subpanel_count": len(cell_paths),
                    "cells": [path.name for path in cell_paths],
                }
                metadata.append(page_metadata)
                state["completed"][str(prompt.index)] = {
                    "checksum": sha256_file(destination),
                    "metadata": page_metadata,
                    "cells": [
                        {"path": path.name, "checksum": sha256_file(path)} for path in cell_paths
                    ],
                }
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                previous_page = destination
            audio_duration = self.ffmpeg.duration(narration_track)
            logical_durations = [max(prompt.end - prompt.start, 0.1) for prompt in prompts]
            logical_total = sum(logical_durations)
            durations = [
                audio_duration * duration / logical_total for duration in logical_durations
            ]
            video = segment_dir / "panel-video.mp4"
            render_signature = {
                "audio_checksum": sha256_file(narration_track),
                "images": [sha256_file(image) for image in images],
                "durations": durations,
            }
            render_state_path = segment_dir / "render.json"
            render_state = self._generation_state(render_state_path, render_signature)
            if not (
                video.exists()
                and render_state.get("completed", {}).get("checksum") == sha256_file(video)
            ):
                self.ffmpeg.compose_art_video_with_audio(
                    images,
                    durations,
                    narration_track,
                    video,
                )
                render_state["completed"] = {"checksum": sha256_file(video)}
                render_state_path.write_text(
                    json.dumps(render_state, indent=2),
                    encoding="utf-8",
                )
            section_videos.append(video)
            manifest.add_artifact(
                Artifact(
                    kind="document_section_video",
                    path=video.relative_to(self.store.directory(job_id)).as_posix(),
                    segment_id=section.id,
                    checksum=sha256_file(video),
                    metadata={"images": metadata, "synthetic": True},
                )
            )
            self.store.save(manifest)
        if not section_videos:
            raise ValueError("No approved document sections are available to render")
        final_video = self.store.directory(job_id) / "art" / "document-video.mp4"
        final_signature = {
            "sections": [sha256_file(video) for video in section_videos],
        }
        final_state_path = self.store.directory(job_id) / "art" / "document-render.json"
        final_state = self._generation_state(final_state_path, final_signature)
        if not (
            final_video.exists()
            and final_state.get("completed", {}).get("checksum") == sha256_file(final_video)
        ):
            self.ffmpeg.concat_videos(section_videos, final_video)
            final_state["completed"] = {"checksum": sha256_file(final_video)}
            final_state_path.write_text(
                json.dumps(final_state, indent=2),
                encoding="utf-8",
            )
        manifest.add_artifact(
            Artifact(
                kind="illustrated_video",
                path=final_video.relative_to(self.store.directory(job_id)).as_posix(),
                checksum=sha256_file(final_video),
                metadata={"source_kind": "document", "section_count": len(section_videos)},
            )
        )
        manifest.stage = Stage.RENDERED
        self.store.save(manifest)
        return final_video

    def render_art(
        self,
        job_id: str,
        provider: ImageProvider,
        scene_analyzer: GeminiSceneAnalyzer,
        *,
        style: str,
        seconds_per_panel: float = 8,
        min_panels: int = 3,
        max_panels: int = 12,
        subpanels_per_image: int = 4,
        representative_frame_count: int = 3,
        reference_frame_max_width: int = 1024,
        segment_ids: set[str] | None = None,
        moderation_fallback_enabled: bool = True,
        moderation_final_action: str = "placeholder",
    ) -> list[Path]:
        if moderation_final_action not in {"placeholder", "fail"}:
            raise ValueError("Invalid moderation final action")
        if not 1 <= subpanels_per_image <= 4:
            raise ValueError("subpanels_per_image must be between 1 and 4")
        logger.info("Starting art rendering job_id=%s", job_id)
        manifest = self.store.load(job_id)
        manifest.require_approval("spans")
        source = self._source(manifest)
        source_checksum = sha256_file(source)
        transcript = self.transcript(job_id)
        outputs: list[Path] = []
        for segment in manifest.segments:
            if not segment.approved or (segment_ids is not None and segment.id not in segment_ids):
                continue
            base_prompts = panel_prompts(
                segment,
                transcript=transcript,
                seconds_per_panel=seconds_per_panel,
                min_panels=min_panels,
                max_panels=max_panels,
                style=style,
            )
            segment_dir = self.store.directory(job_id) / "art" / segment.id
            references, references_path = self._reference_frames(
                job_id,
                segment_dir,
                source,
                source_checksum,
                segment,
                base_prompts,
                representative_frame_count,
                reference_frame_max_width,
            )
            manifest.add_artifact(
                Artifact(
                    kind="reference_frames",
                    path=references_path.relative_to(self.store.directory(job_id)).as_posix(),
                    segment_id=segment.id,
                    checksum=sha256_file(references_path),
                )
            )
            context, context_path = self._scene_context(
                job_id,
                segment_dir,
                source_checksum,
                references,
                scene_analyzer,
                segment.context,
            )
            manifest.add_artifact(
                Artifact(
                    kind="scene_context",
                    path=context_path.relative_to(self.store.directory(job_id)).as_posix(),
                    segment_id=segment.id,
                    checksum=sha256_file(context_path),
                )
            )
            self.store.save(manifest)
            prompts = panel_prompts(
                segment,
                transcript=transcript,
                scene_context=context,
                seconds_per_panel=seconds_per_panel,
                min_panels=min_panels,
                max_panels=max_panels,
                style=style,
            )
            generation_prompts: list[PanelPrompt] = (
                comic_page_prompts(prompts, max_subpanels=subpanels_per_image)
                if subpanels_per_image > 1
                else prompts
            )
            prompt_values = [prompt.model_dump(mode="json") for prompt in generation_prompts]
            self.store.write_json(
                job_id,
                f"art/{segment.id}/prompts.json",
                prompt_values,
            )
            signature = {
                "source_checksum": source_checksum,
                "scene_model": scene_analyzer.cache_key,
                "scene_context": context.model_dump(mode="json"),
                "image_provider": provider.cache_key,
                "layout": {
                    "version": "composite-v1",
                    "subpanels_per_image": subpanels_per_image,
                },
                "moderation_policy": {
                    "version": "compliant-v1",
                    "enabled": moderation_fallback_enabled,
                    "final_action": moderation_final_action,
                },
                "prompts": prompt_values,
                "references": [reference.model_dump(mode="json") for reference in references],
            }
            state_path = segment_dir / "generation.json"
            state = self._generation_state(state_path, signature)
            image_paths: list[Path] = []
            metadata: list[dict[str, Any]] = []
            reference_paths = {
                reference.index: segment_dir / reference.path for reference in references
            }
            previous_page: Path | None = None
            for prompt in generation_prompts:
                is_composite = isinstance(prompt, ComicPagePrompt)
                destination = segment_dir / (
                    f"page-{prompt.index:03d}.png"
                    if is_composite
                    else f"panel-{prompt.index:02d}.png"
                )
                completed = state["completed"].get(str(prompt.index))
                if (
                    completed
                    and destination.exists()
                    and completed.get("checksum") == sha256_file(destination)
                ):
                    panel_metadata = completed["metadata"]
                    logger.info(
                        "Reusing art panel job_id=%s segment_id=%s panel=%d",
                        job_id,
                        segment.id,
                        prompt.index,
                    )
                else:
                    panel_items = prompt.subpanels if is_composite else [prompt]
                    selected = list(
                        dict.fromkeys(
                            path
                            for panel_item in panel_items
                            for path in self._panel_references(
                                panel_item.index,
                                panel_item.subject_ids,
                                panel_item.reference_indices,
                                context,
                                references,
                                reference_paths,
                            )
                        )
                    )[:15]
                    try:
                        panel_metadata = self._generate_panel_with_fallback(
                            provider,
                            prompt,
                            destination,
                            selected,
                            previous_page,
                            enabled=moderation_fallback_enabled,
                            final_action=moderation_final_action,
                        )
                    except ImageModerationBlocked as error:
                        state.setdefault("failed", {})[str(prompt.index)] = {
                            "moderation_fallback": {
                                "policy_version": "compliant-v1",
                                "attempts": error.fallback_attempts,
                                "final_disposition": "failed",
                            }
                        }
                        state_path.write_text(
                            json.dumps(state, indent=2),
                            encoding="utf-8",
                        )
                        raise
                    state["completed"][str(prompt.index)] = {
                        "checksum": sha256_file(destination),
                        "metadata": panel_metadata,
                        "references": [
                            path.relative_to(segment_dir).as_posix() for path in selected
                        ],
                    }
                    state_path.write_text(
                        json.dumps(state, indent=2),
                        encoding="utf-8",
                    )
                    logger.info(
                        "Generated art image job_id=%s segment_id=%s image=%d",
                        job_id,
                        segment.id,
                        prompt.index,
                    )
                if is_composite:
                    cell_paths = self._comic_page_cells(
                        destination,
                        segment_dir,
                        prompt,
                        completed,
                    )
                    image_paths.extend(cell_paths)
                else:
                    cell_paths = [destination]
                    image_paths.append(destination)
                page_metadata = {
                    **panel_metadata,
                    "subpanel_count": len(cell_paths),
                    "cells": [path.name for path in cell_paths],
                }
                metadata.append(page_metadata)
                state["completed"][str(prompt.index)] = {
                    **state["completed"].get(str(prompt.index), {}),
                    "checksum": sha256_file(destination),
                    "metadata": page_metadata,
                    "cells": [
                        {"path": path.name, "checksum": sha256_file(path)} for path in cell_paths
                    ],
                }
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                previous_page = destination
            durations = [max(prompt.end - prompt.start, 0.1) for prompt in prompts]
            video = self.store.directory(job_id) / "art" / segment.id / "panel-video.mp4"
            self.ffmpeg.compose_art_video(
                image_paths,
                durations,
                source,
                segment.start,
                video,
            )
            relative = video.relative_to(self.store.directory(job_id)).as_posix()
            manifest.add_artifact(
                Artifact(
                    kind="illustrated_video",
                    path=relative,
                    segment_id=segment.id,
                    checksum=sha256_file(video),
                    metadata={"images": metadata, "synthetic": True},
                )
            )
            outputs.append(video)
            manifest.stage = Stage.RENDERED
            self.store.save(manifest)
            logger.info(
                "Rendered illustrated video job_id=%s segment_id=%s path=%s",
                job_id,
                segment.id,
                video,
            )
        logger.info("Completed art rendering job_id=%s output_count=%d", job_id, len(outputs))
        return outputs

    def _comic_page_cells(
        self,
        page: Path,
        segment_dir: Path,
        prompt: ComicPagePrompt,
        completed: dict[str, Any] | None,
    ) -> list[Path]:
        expected_names = [
            f"page-{prompt.index:03d}-cell-{index}.png"
            for index in range(1, len(prompt.subpanels) + 1)
        ]
        expected_paths = [segment_dir / name for name in expected_names]
        cached_cells = completed.get("cells", []) if completed else []
        cached_by_name = {
            str(item.get("path")): str(item.get("checksum"))
            for item in cached_cells
            if isinstance(item, dict)
        }
        if all(
            path.is_file() and cached_by_name.get(path.name) == sha256_file(path)
            for path in expected_paths
        ):
            return expected_paths
        return self.ffmpeg.split_comic_page(
            page,
            segment_dir,
            page_index=prompt.index,
            cell_count=len(prompt.subpanels),
        )

    def _generate_panel_with_fallback(
        self,
        provider: ImageProvider,
        prompt: PanelPrompt,
        destination: Path,
        references: list[Path],
        previous_panel: Path | None,
        *,
        enabled: bool,
        final_action: str,
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        normal_reference_count = len(references) + int(
            previous_panel is not None and previous_panel.is_file()
        )
        try:
            return provider.generate(
                prompt,
                destination,
                reference_images=references,
                previous_panel=previous_panel,
            )
        except ImageModerationBlocked as error:
            attempts.append(
                self._moderation_attempt(
                    "normal",
                    error,
                    normal_reference_count,
                )
            )
            if not enabled:
                error.fallback_attempts = attempts
                raise

        benign_prompt = safe_panel_prompt(prompt)
        try:
            metadata = provider.generate(
                benign_prompt,
                destination,
                reference_images=references,
                previous_panel=previous_panel,
            )
            return self._with_fallback_metadata(
                metadata,
                attempts,
                "safe_reference",
            )
        except ImageModerationBlocked as error:
            attempts.append(
                self._moderation_attempt(
                    "safe_reference",
                    error,
                    normal_reference_count,
                )
            )

        try:
            metadata = provider.generate(
                benign_prompt,
                destination,
                reference_images=[],
                previous_panel=None,
            )
            return self._with_fallback_metadata(
                metadata,
                attempts,
                "safe_text_only",
            )
        except ImageModerationBlocked as error:
            attempts.append(self._moderation_attempt("safe_text_only", error, 0))
            if final_action == "fail":
                error.fallback_attempts = attempts
                raise

        self.ffmpeg.create_placeholder_image(destination)
        return self._with_fallback_metadata(
            {
                "provider": "local",
                "model": "neutral-placeholder",
                "mode": "moderation_placeholder",
                "reference_count": 0,
                "placeholder": True,
            },
            attempts,
            "placeholder",
        )

    @staticmethod
    def _moderation_attempt(
        level: str,
        error: ImageModerationBlocked,
        reference_count: int,
    ) -> dict[str, Any]:
        return {
            "level": level,
            "status": "blocked",
            "reference_count": reference_count,
            "moderation": error.audit_metadata(),
        }

    @staticmethod
    def _with_fallback_metadata(
        metadata: dict[str, Any],
        attempts: list[dict[str, Any]],
        disposition: str,
    ) -> dict[str, Any]:
        return {
            **metadata,
            "moderation_fallback": {
                "policy_version": "compliant-v1",
                "attempts": attempts,
                "final_disposition": disposition,
            },
        }

    def _reference_frames(
        self,
        job_id: str,
        segment_dir: Path,
        source: Path,
        source_checksum: str,
        segment: Segment,
        prompts: list[PanelPrompt],
        representative_frame_count: int,
        max_width: int,
    ) -> tuple[list[FrameReference], Path]:
        specs: list[dict[str, Any]] = []
        for prompt in prompts:
            specs.append(
                {
                    "panel_index": prompt.index,
                    "timestamp": round((prompt.start + prompt.end) / 2, 6),
                    "label": f"panel-{prompt.index:02d}",
                }
            )
        for index in range(representative_frame_count):
            timestamp = segment.start + segment.duration * (
                (index + 1) / (representative_frame_count + 1)
            )
            if any(abs(timestamp - item["timestamp"]) < 0.15 for item in specs):
                continue
            specs.append(
                {
                    "panel_index": 0,
                    "timestamp": round(timestamp, 6),
                    "label": f"representative-{index + 1:02d}",
                }
            )
        request = {
            "source_checksum": source_checksum,
            "max_width": max_width,
            "frames": specs,
        }
        metadata_path = segment_dir / "references" / "references.json"
        if metadata_path.exists():
            try:
                existing = json.loads(metadata_path.read_text(encoding="utf-8"))
                if all(existing.get(key) == value for key, value in request.items()):
                    references = [
                        FrameReference.model_validate(value)
                        for value in existing.get("references", [])
                    ]
                    if references and all(
                        (segment_dir / reference.path).is_file()
                        and sha256_file(segment_dir / reference.path) == reference.checksum
                        for reference in references
                    ):
                        logger.info(
                            "Reusing reference frames job_id=%s segment_id=%s",
                            job_id,
                            segment.id,
                        )
                        return references, metadata_path
            except json.JSONDecodeError, ValueError:
                logger.warning(
                    "Invalid reference metadata job_id=%s segment_id=%s",
                    job_id,
                    segment.id,
                )
        references: list[FrameReference] = []
        for index, spec in enumerate(specs, start=1):
            filename = f"{spec['label']}-{round(spec['timestamp'] * 1000):010d}.jpg"
            destination = segment_dir / "references" / filename
            self.ffmpeg.extract_frame(
                source,
                destination,
                spec["timestamp"],
                max_width=max_width,
            )
            references.append(
                FrameReference(
                    index=index,
                    panel_index=spec["panel_index"],
                    timestamp=spec["timestamp"],
                    path=destination.relative_to(segment_dir).as_posix(),
                    checksum=sha256_file(destination),
                )
            )
        payload = {
            **request,
            "references": [reference.model_dump(mode="json") for reference in references],
        }
        metadata_path = self.store.write_json(
            job_id,
            f"art/{segment.id}/references/references.json",
            payload,
        )
        return references, metadata_path

    def _scene_context(
        self,
        job_id: str,
        segment_dir: Path,
        source_checksum: str,
        references: list[FrameReference],
        analyzer: GeminiSceneAnalyzer,
        segment_context: str,
    ) -> tuple[SegmentSceneContext, Path]:
        context_path = segment_dir / "scene-context.json"
        if context_path.exists():
            try:
                artifact = SceneContextArtifact.model_validate_json(
                    context_path.read_text(encoding="utf-8")
                )
                if (
                    artifact.model in {analyzer.model, analyzer.cache_key}
                    and artifact.source_checksum == source_checksum
                    and artifact.references == references
                ):
                    analyzer.normalize_context(artifact.context, references)
                    if artifact.model != analyzer.cache_key:
                        artifact.model = analyzer.cache_key
                        context_path.write_text(
                            artifact.model_dump_json(indent=2),
                            encoding="utf-8",
                        )
                    logger.info(
                        "Reusing scene context job_id=%s path=%s",
                        job_id,
                        context_path,
                    )
                    return artifact.context, context_path
            except ValueError:
                logger.warning(
                    "Invalid scene context job_id=%s path=%s",
                    job_id,
                    context_path,
                )
        context = analyzer.analyze(
            [(reference, segment_dir / reference.path) for reference in references],
            segment_context=segment_context,
        )
        artifact = SceneContextArtifact(
            model=analyzer.cache_key,
            source_checksum=source_checksum,
            references=references,
            context=context,
        )
        context_path = self.store.write_json(
            job_id,
            f"art/{segment_dir.name}/scene-context.json",
            artifact.model_dump(mode="json"),
        )
        return context, context_path

    @staticmethod
    def _generation_state(
        state_path: Path,
        signature: dict[str, Any],
    ) -> dict[str, Any]:
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("signature") == signature:
                    state.setdefault("completed", {})
                    return state
            except json.JSONDecodeError:
                pass
        state = {"signature": signature, "completed": {}}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state

    @staticmethod
    def _panel_references(
        panel_index: int,
        subject_ids: list[str],
        explicit_indices: list[int],
        context: SegmentSceneContext,
        references: list[FrameReference],
        reference_paths: dict[int, Path],
    ) -> list[Path]:
        requested: list[int] = []
        requested.extend(explicit_indices)
        panel_context = context.panel(panel_index)
        for subject in context.subjects:
            if panel_context is None or subject.id in subject_ids:
                requested.extend(subject.reference_indices)
        requested.extend(
            reference.index for reference in references if reference.panel_index == panel_index
        )
        if not requested:
            requested.extend(reference.index for reference in references)
        selected: list[Path] = []
        seen: set[int] = set()
        for index in requested:
            path = reference_paths.get(index)
            if index in seen or path is None:
                continue
            seen.add(index)
            selected.append(path)
            if len(selected) == 15:
                break
        return selected

    def _story_reference_images(
        self,
        job_id: str,
        story: StoryBible,
        provider: ImageProvider,
        *,
        style: str,
        moderation_fallback_enabled: bool,
        moderation_final_action: str,
    ) -> dict[str, Path]:
        root = self.store.directory(job_id) / "art" / "story-references"
        signature = {
            "provider": provider.cache_key,
            "story": story.model_dump(mode="json"),
            "style": style,
            "policy": {
                "version": "compliant-v1",
                "enabled": moderation_fallback_enabled,
                "final_action": moderation_final_action,
            },
        }
        state_path = root / "generation.json"
        state = self._generation_state(state_path, signature)
        references: dict[str, Path] = {}
        entries = [
            (
                character.id,
                (
                    f"{style}. Full-body neutral character reference sheet for "
                    f"{character.name}. {character.visual_description}. "
                    f"Clothing: {character.clothing}. Plain background, no text."
                ),
            )
            for character in story.characters
        ]
        entries.extend(
            (
                location.id,
                (
                    f"{style}. Neutral environment reference sheet for {location.name}. "
                    f"{location.description}. No people, no text."
                ),
            )
            for location in story.locations
        )
        for index, (entry_id, description) in enumerate(entries, start=1):
            destination = root / f"{entry_id}.png"
            completed = state["completed"].get(entry_id)
            reference_prompt = PanelPrompt(
                index=index,
                start=0,
                end=1,
                prompt=description,
                safe_prompt=(
                    "Clean non-explicit editorial reference illustration. Ordinary fully "
                    "covering clothing when people are present. Plain background, no text."
                ),
            )
            if not (
                completed
                and destination.exists()
                and completed.get("checksum") == sha256_file(destination)
            ):
                metadata = self._generate_panel_with_fallback(
                    provider,
                    reference_prompt,
                    destination,
                    [],
                    None,
                    enabled=moderation_fallback_enabled,
                    final_action=moderation_final_action,
                )
                state["completed"][entry_id] = {
                    "checksum": sha256_file(destination),
                    "metadata": metadata,
                }
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            references[entry_id] = destination
        return references

    def document_preflight(
        self,
        job_id: str,
        *,
        words_per_panel: int = 90,
        subpanels_per_image: int = 4,
    ) -> dict[str, Any]:
        if words_per_panel < 1:
            raise ValueError("words_per_panel must be positive")
        if not 1 <= subpanels_per_image <= 4:
            raise ValueError("subpanels_per_image must be between 1 and 4")
        manifest = self.store.load(job_id)
        document = self._load_document_model(
            manifest,
            "extracted_document_path",
            ExtractedDocument,
        )
        sections = self._document_sections(manifest) if manifest.sections_path else []
        selection = (
            self._load_document_model(
                manifest,
                "content_selection_path",
                DocumentContentSelection,
            )
            if manifest.content_selection_path
            else None
        )
        script = (
            self._load_document_model(manifest, "script_path", NarrationScript)
            if manifest.script_path
            else None
        )
        narration_characters = sum(len(line.text) for line in script.lines) if script else 0
        estimated_words = (
            sum(len(line.text.split()) for line in script.lines)
            if script
            else document.metadata.word_count
        )
        section_panel_counts = [
            max(1, math.ceil(len(section.text.split()) / words_per_panel)) for section in sections
        ]
        estimated_panels = sum(section_panel_counts)
        estimated_comic_pages = sum(
            math.ceil(panel_count / subpanels_per_image) for panel_count in section_panel_counts
        )
        story_reference_requests = 0
        if manifest.story_bible_path:
            story = self._load_document_model(
                manifest,
                "story_bible_path",
                StoryBible,
            )
            story_reference_requests = len(story.characters) + len(story.locations)
        return {
            "document_words": document.metadata.word_count,
            "document_pages": document.metadata.page_count,
            "content_start_page": (selection.requested_start_page if selection else None),
            "content_start_char": selection.content_start_char if selection else 0,
            "excluded_front_matter_characters": (
                selection.excluded_end_char - selection.excluded_start_char if selection else 0
            ),
            "selected_title": selection.title.value if selection and selection.title else "",
            "selected_author": (selection.author.value if selection and selection.author else ""),
            "content_selection_warnings": selection.warnings if selection else [],
            "core_document_words": sum(len(section.text.split()) for section in sections),
            "section_count": len(sections),
            "narration_characters": narration_characters,
            "estimated_duration_minutes": round(estimated_words / 150, 1),
            "estimated_panels": estimated_panels,
            "estimated_visual_events": estimated_panels,
            "estimated_comic_pages": estimated_comic_pages,
            "subpanels_per_image": subpanels_per_image,
            "estimated_tts_requests": len(script.lines) if script else 0,
            "ocr_pages": sum(page.extraction_method == "gemini_ocr" for page in document.pages),
            "estimated_story_reference_requests": story_reference_requests,
            "estimated_image_requests": (
                story_reference_requests + estimated_comic_pages if manifest.story_bible_path else 0
            ),
        }

    def _document_sections(self, manifest: Any) -> list[DocumentSection]:
        if not manifest.sections_path:
            raise LookupError("Job has no document sections")
        path = self.store.directory(manifest.id) / manifest.sections_path
        return [
            DocumentSection.model_validate(value)
            for value in json.loads(path.read_text(encoding="utf-8"))
        ]

    def _load_document_model(
        self,
        manifest: Any,
        field: str,
        model: Any,
    ) -> Any:
        relative = getattr(manifest, field)
        if not relative:
            raise LookupError(f"Job has no {field}")
        path = self.store.directory(manifest.id) / relative
        return model.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _approximate_alignment(
        text: str,
        duration_ms: int,
        speaker: str,
    ) -> list[Word]:
        tokens = text.split()
        if not tokens:
            return []
        step = duration_ms / len(tokens)
        return [
            Word(
                text=token,
                start_ms=round(index * step),
                end_ms=round((index + 1) * step),
                speaker=speaker,
            )
            for index, token in enumerate(tokens)
        ]

    def transcript(self, job_id: str) -> Transcript:
        manifest = self.store.load(job_id)
        if not manifest.transcript_path:
            raise LookupError("Job has no transcript")
        path = self.store.directory(job_id) / manifest.transcript_path
        return Transcript.model_validate_json(path.read_text(encoding="utf-8"))

    def _source(self, manifest) -> Path:
        if not manifest.source_path:
            raise LookupError("Job has no ingested source")
        path = self.store.directory(manifest.id) / manifest.source_path
        if not path.exists():
            raise FileNotFoundError(path)
        return path
