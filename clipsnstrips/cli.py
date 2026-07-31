from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from clipsnstrips.analysis.highlights import GeminiHighlightAnalyzer
from clipsnstrips.analysis.transcription import AssemblyAITranscriber
from clipsnstrips.art.providers import OpenAIImageProvider
from clipsnstrips.config import Settings
from clipsnstrips.ingest.sources import download_youtube, ingest_local
from clipsnstrips.jobs import JobStore
from clipsnstrips.logging_config import configure_logging
from clipsnstrips.media.ffmpeg import FFmpeg
from clipsnstrips.models import (
    JobManifest,
    RenderOptions,
    RightsEvidence,
    VideoCandidate,
)
from clipsnstrips.pipeline import Pipeline
from clipsnstrips.review import record_approval, set_segment_approval
from clipsnstrips.youtube.discovery import YouTubeDiscovery
from clipsnstrips.youtube.oauth import youtube_credentials
from clipsnstrips.youtube.upload import YouTubeUploader

app = typer.Typer(no_args_is_help=True, help="Build reviewed clips from authorized media.")
logger = logging.getLogger(__name__)


def context() -> tuple[Settings, JobStore]:
    settings = Settings()
    configure_logging(
        settings.effective_log_dir,
        level=settings.log_level,
        filename=settings.log_filename,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    logger.debug("Application context initialized output_dir=%s", settings.output_dir)
    return settings, JobStore(settings.output_dir)


def print_json(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


@app.command()
def discover(
    limit: int = 10,
    region: str | None = None,
    category: str | None = None,
    dry_run: bool = True,
) -> None:
    """Discover popular videos; dry-run avoids creating job folders."""
    settings, store = context()
    service = YouTubeDiscovery(
        settings.require("youtube_api_key"),
        settings.owned_channel_ids,
    )
    candidates = service.most_popular(
        region=region or settings.youtube_region,
        category=category or settings.youtube_category,
        limit=limit,
    )
    if dry_run:
        print_json([item.model_dump(mode="json") for item in candidates])
        return
    job_ids = []
    for candidate in candidates:
        manifest = store.create(JobManifest(source=candidate, rights_state=candidate.rights_state))
        store.write_json(
            manifest.id,
            "source/youtube_metadata.json",
            candidate.model_dump(mode="json"),
        )
        job_ids.append(manifest.id)
    print_json({"created_jobs": job_ids})


@app.command("create-local")
def create_local(title: str = "Local media") -> None:
    """Create a review job for user-supplied media."""
    _, store = context()
    manifest = store.create(
        JobManifest(
            source=VideoCandidate(
                video_id="local",
                title=title,
                channel_id="local",
                risk_reasons=["Local media requires an authorization attestation"],
            )
        )
    )
    typer.echo(manifest.id)


@app.command()
def approve(
    job_id: str,
    purpose: str,
    reviewer: str,
    notes: str,
    approved: bool = True,
    evidence_kind: str | None = None,
    evidence_value: str | None = None,
    evidence_source: str | None = None,
) -> None:
    """Record an auditable ingest, spans, rights, output, or publish decision."""
    _, store = context()
    if evidence_kind and evidence_value:
        manifest = store.load(job_id)
        manifest.rights_evidence.append(
            RightsEvidence(
                kind=evidence_kind,
                value=evidence_value,
                source=evidence_source,
            )
        )
        store.save(manifest)
    record_approval(
        store,
        job_id,
        purpose=purpose,
        reviewer=reviewer,
        approved=approved,
        notes=notes,
    )


@app.command("ingest-local")
def ingest_local_command(job_id: str, source: Path) -> None:
    _, store = context()
    typer.echo(ingest_local(store, store.load(job_id), source))


@app.command("download-youtube")
def download_youtube_command(job_id: str, url: str) -> None:
    """Download only a source whose authorization is documented in the job."""
    _, store = context()
    typer.echo(download_youtube(store, store.load(job_id), url))


@app.command()
def analyze(job_id: str) -> None:
    settings, store = context()
    pipeline = Pipeline(
        store,
        FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary),
    )
    pipeline.analyze(
        job_id,
        AssemblyAITranscriber(settings.require("assemblyai_api_key")),
        GeminiHighlightAnalyzer(
            settings.require("gemini_api_key"),
            model=settings.gemini_model,
        ),
        min_seconds=settings.min_clip_seconds,
        max_seconds=settings.max_clip_seconds,
    )
    manifest = store.load(job_id)
    print_json([segment.model_dump(mode="json") for segment in manifest.segments])


@app.command("select-spans")
def select_spans(job_id: str, segment_ids: list[str]) -> None:
    set_segment_approval(context()[1], job_id, set(segment_ids))


@app.command("render-clips")
def render_clips(
    job_id: str,
    vertical: bool = False,
) -> None:
    settings, store = context()
    pipeline = Pipeline(store, FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary))
    print_json(
        [
            str(path)
            for path in pipeline.render_clips(
                job_id,
                RenderOptions(vertical=vertical),
            )
        ]
    )


@app.command("render-art")
def render_art(
    job_id: str,
    style: str = "cinematic editorial comic, clean ink, rich color, no text",
) -> None:
    settings, store = context()
    pipeline = Pipeline(store, FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary))
    outputs = pipeline.render_art(
        job_id,
        OpenAIImageProvider(settings.require("openai_api_key")),
        style=style,
    )
    print_json([str(path) for path in outputs])


@app.command("upload-private")
def upload_private(
    job_id: str,
    artifact: Path,
    title: str,
    description: str,
) -> None:
    settings, store = context()
    manifest = store.load(job_id)
    credentials = youtube_credentials(
        settings.youtube_oauth_client_file,
        settings.youtube_oauth_token_file,
    )
    artifact_path = artifact if artifact.is_absolute() else store.directory(job_id) / artifact
    record = YouTubeUploader(credentials).upload_private(
        manifest,
        artifact_path,
        store.directory(job_id),
        title=title,
        description=description,
    )
    store.save(manifest)
    print_json(record.model_dump(mode="json"))


@app.command()
def publish(job_id: str, video_id: str) -> None:
    settings, store = context()
    manifest = store.load(job_id)
    credentials = youtube_credentials(
        settings.youtube_oauth_client_file,
        settings.youtube_oauth_token_file,
    )
    YouTubeUploader(credentials).publish(manifest, video_id)
    store.save(manifest)


@app.command()
def show(job_id: str) -> None:
    print_json(context()[1].load(job_id).model_dump(mode="json"))


@app.command()
def doctor() -> None:
    settings, _ = context()
    FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary).check()
    typer.echo("FFmpeg and FFprobe are available.")


if __name__ == "__main__":
    app()
