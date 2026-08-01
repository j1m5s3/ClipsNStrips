from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from clipsnstrips.analysis.highlights import (
    GeminiHighlightAnalyzer,
    scaled_candidate_limit,
)
from clipsnstrips.analysis.scene_context import (
    FrameReference,
    GeminiSceneAnalyzer,
    SceneContextArtifact,
    SegmentSceneContext,
)
from clipsnstrips.analysis.transcription import AssemblyAITranscriber
from clipsnstrips.art.prompts import PanelPrompt, panel_prompts, safe_panel_prompt
from clipsnstrips.art.providers import ImageModerationBlocked, ImageProvider
from clipsnstrips.jobs import JobStore, sha256_file
from clipsnstrips.media.ffmpeg import FFmpeg
from clipsnstrips.models import Artifact, RenderOptions, Segment, Stage, Transcript

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
        representative_frame_count: int = 3,
        reference_frame_max_width: int = 1024,
        segment_ids: set[str] | None = None,
        moderation_fallback_enabled: bool = True,
        moderation_final_action: str = "placeholder",
    ) -> list[Path]:
        if moderation_final_action not in {"placeholder", "fail"}:
            raise ValueError("Invalid moderation final action")
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
            prompt_values = [prompt.model_dump(mode="json") for prompt in prompts]
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
            for prompt in prompts:
                destination = segment_dir / f"panel-{prompt.index:02d}.png"
                completed = state["completed"].get(str(prompt.index))
                if (
                    completed
                    and destination.exists()
                    and completed.get("checksum") == sha256_file(destination)
                ):
                    metadata.append(completed["metadata"])
                    logger.info(
                        "Reusing art panel job_id=%s segment_id=%s panel=%d",
                        job_id,
                        segment.id,
                        prompt.index,
                    )
                else:
                    selected = self._panel_references(
                        prompt.index,
                        prompt.subject_ids,
                        prompt.reference_indices,
                        context,
                        references,
                        reference_paths,
                    )
                    previous = (
                        segment_dir / f"panel-{prompt.index - 1:02d}.png"
                        if prompt.index > 1
                        else None
                    )
                    try:
                        panel_metadata = self._generate_panel_with_fallback(
                            provider,
                            prompt,
                            destination,
                            selected,
                            previous,
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
                    metadata.append(panel_metadata)
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
                        "Generated art panel job_id=%s segment_id=%s panel=%d",
                        job_id,
                        segment.id,
                        prompt.index,
                    )
                image_paths.append(destination)
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
