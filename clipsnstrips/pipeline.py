from __future__ import annotations

import logging
from pathlib import Path

from clipsnstrips.analysis.highlights import GeminiHighlightAnalyzer
from clipsnstrips.analysis.transcription import AssemblyAITranscriber
from clipsnstrips.art.prompts import panel_prompts
from clipsnstrips.art.providers import ImageProvider
from clipsnstrips.jobs import JobStore, sha256_file
from clipsnstrips.media.ffmpeg import FFmpeg
from clipsnstrips.models import Artifact, RenderOptions, Stage, Transcript

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
    ) -> None:
        logger.info("Starting analysis job_id=%s", job_id)
        manifest = self.store.load(job_id)
        manifest.require_approval("ingest")
        source = self._source(manifest)
        probe = self.ffmpeg.probe(source)
        raw_duration = probe.get("format", {}).get("duration")
        media_duration = float(raw_duration) if raw_duration is not None else None
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
        *,
        style: str,
    ) -> list[Path]:
        logger.info("Starting art rendering job_id=%s", job_id)
        manifest = self.store.load(job_id)
        manifest.require_approval("spans")
        source = self._source(manifest)
        outputs: list[Path] = []
        for segment in manifest.segments:
            if not segment.approved:
                continue
            prompts = panel_prompts(segment, style=style)
            image_paths: list[Path] = []
            metadata: list[dict[str, str]] = []
            for prompt in prompts:
                destination = (
                    self.store.directory(job_id)
                    / "art"
                    / segment.id
                    / f"panel-{prompt.index:02d}.png"
                )
                metadata.append(provider.generate(prompt, destination))
                image_paths.append(destination)
                logger.info(
                    "Generated art panel job_id=%s segment_id=%s panel=%d",
                    job_id,
                    segment.id,
                    prompt.index,
                )
            self.store.write_json(
                job_id,
                f"art/{segment.id}/prompts.json",
                [prompt.model_dump(mode="json") for prompt in prompts],
            )
            durations = [max(prompt.end - prompt.start, 0.1) for prompt in prompts]
            video = self.store.directory(job_id) / "art" / f"{segment.id}.mp4"
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
            logger.info(
                "Rendered illustrated video job_id=%s segment_id=%s path=%s",
                job_id,
                segment.id,
                video,
            )
        manifest.stage = Stage.RENDERED
        self.store.save(manifest)
        logger.info("Completed art rendering job_id=%s output_count=%d", job_id, len(outputs))
        return outputs

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
