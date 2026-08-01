from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from clipsnstrips.analysis.document_content import GeminiFrontMatterAnalyzer
from clipsnstrips.analysis.document_sections import GeminiDocumentScriptAnalyzer
from clipsnstrips.analysis.highlights import GeminiHighlightAnalyzer
from clipsnstrips.analysis.scene_context import GeminiSceneAnalyzer, GeminiStoryAnalyzer
from clipsnstrips.analysis.transcription import AssemblyAITranscriber
from clipsnstrips.art.providers import OpenAIImageProvider
from clipsnstrips.config import Settings
from clipsnstrips.ingest.documents import (
    GeminiDocumentOCR,
    extract_document,
    ingest_document,
)
from clipsnstrips.ingest.sources import download_youtube, ingest_local
from clipsnstrips.jobs import JobStore, build_job_id
from clipsnstrips.logging_config import configure_logging
from clipsnstrips.media.ffmpeg import FFmpeg
from clipsnstrips.models import (
    JobManifest,
    RenderOptions,
    RightsEvidence,
    ScriptMode,
    SourceKind,
    VideoCandidate,
)
from clipsnstrips.narration.providers import ElevenLabsNarrationProvider
from clipsnstrips.pipeline import Pipeline
from clipsnstrips.review import (
    bypass_processing_gate,
    record_approval,
    set_segment_approval,
)
from clipsnstrips.youtube.discovery import YouTubeDiscovery
from clipsnstrips.youtube.oauth import youtube_credentials
from clipsnstrips.youtube.upload import YouTubeUploader
from clipsnstrips.youtube.urls import video_id_from_url

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
        manifest = store.create(
            JobManifest(
                id=build_job_id(candidate.title, candidate.channel_title),
                source=candidate,
                rights_state=candidate.rights_state,
            )
        )
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
    candidate = VideoCandidate(
        video_id="local",
        title=title,
        channel_id="local",
        channel_title="Local",
        risk_reasons=["Local media requires an authorization attestation"],
    )
    manifest = store.create(
        JobManifest(
            id=build_job_id(candidate.title, candidate.channel_title),
            source=candidate,
        )
    )
    typer.echo(manifest.id)


@app.command("process-youtube")
def process_youtube(
    url: str,
    reviewer: str | None = None,
    vertical: bool = True,
    art: bool = False,
    no_approval: bool = typer.Option(
        False,
        "--no-approval",
        help="Bypass processing approvals and record the override.",
    ),
) -> None:
    """Interactively review and process one authorized YouTube URL."""
    settings, store = context()
    video_id = video_id_from_url(url)
    logger.info("Starting interactive YouTube workflow video_id=%s", video_id)
    discovery = YouTubeDiscovery(
        settings.require("youtube_api_key"),
        settings.owned_channel_ids,
    )
    candidate = discovery.by_id(video_id)
    manifest = store.create(
        JobManifest(
            id=build_job_id(candidate.title, candidate.channel_title),
            source=candidate,
            rights_state=candidate.rights_state,
        )
    )
    store.write_json(
        manifest.id,
        "source/youtube_metadata.json",
        candidate.model_dump(mode="json"),
    )
    print_json(
        {
            "job_id": manifest.id,
            "video_id": candidate.video_id,
            "title": candidate.title,
            "channel": candidate.channel_title,
            "license": candidate.license,
            "risk_score": candidate.risk_score,
            "risk_reasons": candidate.risk_reasons,
        }
    )

    reviewer_name = reviewer or (
        "CLI --no-approval" if no_approval else typer.prompt("Reviewer name")
    )
    if no_approval:
        bypass_processing_gate(store, manifest.id, "ingest")
    else:
        typer.confirm(
            "Do you attest that YouTube and the applicable rights holders authorize "
            "downloading and processing this video?",
            default=False,
            abort=True,
        )
        authorization_notes = typer.prompt("Authorization basis and evidence")
        manifest = store.load(manifest.id)
        manifest.rights_evidence.append(
            RightsEvidence(
                kind="authorization_attestation",
                value="authorized_for_download_and_processing",
                source=f"https://www.youtube.com/watch?v={video_id}",
            )
        )
        store.save(manifest)
        record_approval(
            store,
            manifest.id,
            purpose="ingest",
            reviewer=reviewer_name,
            approved=True,
            notes=authorization_notes,
        )
    download_youtube(store, store.load(manifest.id), url)

    pipeline = Pipeline(
        store,
        FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary),
    )
    pipeline.analyze(
        manifest.id,
        AssemblyAITranscriber(settings.require("assemblyai_api_key")),
        GeminiHighlightAnalyzer(
            settings.require("gemini_api_key"),
            model=settings.gemini_model,
        ),
        min_seconds=settings.min_clip_seconds,
        max_seconds=settings.max_clip_seconds,
        seconds_per_candidate=settings.highlight_seconds_per_candidate,
        min_candidates=settings.highlight_min_candidates,
        max_candidates=settings.highlight_max_candidates,
    )
    analyzed = store.load(manifest.id)
    print_json([segment.model_dump(mode="json") for segment in analyzed.segments])
    if not analyzed.segments:
        raise RuntimeError("Analysis returned no valid candidate segments")

    if no_approval:
        bypass_processing_gate(store, manifest.id, "spans")
    else:
        selection = typer.prompt("Comma-separated segment IDs to render")
        selected_ids = {
            value.strip() for value in selection.replace(" ", ",").split(",") if value.strip()
        }
        if not selected_ids:
            raise typer.BadParameter("Select at least one segment")
        set_segment_approval(store, manifest.id, selected_ids)
        typer.confirm(
            "Have you reviewed the selected spans in their original context?",
            default=False,
            abort=True,
        )
        record_approval(
            store,
            manifest.id,
            purpose="spans",
            reviewer=reviewer_name,
            approved=True,
            notes="Selected spans reviewed interactively in original source context",
        )

    clips = pipeline.render_clips(
        manifest.id,
        RenderOptions(vertical=vertical),
    )
    illustrated: list[Path] = []
    if art:
        if not no_approval:
            typer.confirm(
                "Generate paid AI artwork for the selected spans?",
                default=False,
                abort=True,
            )
        illustrated = pipeline.render_art(
            manifest.id,
            OpenAIImageProvider(
                settings.require("openai_api_key"),
                model=settings.openai_image_model,
                input_fidelity=settings.openai_image_fidelity,
            ),
            GeminiSceneAnalyzer(
                settings.require("gemini_api_key"),
                settings.scene_context_model,
            ),
            style="cinematic editorial comic, clean ink, rich color, no text",
            seconds_per_panel=settings.art_seconds_per_panel,
            min_panels=settings.art_min_panels,
            max_panels=settings.art_max_panels,
            subpanels_per_image=settings.art_subpanels_per_image,
            representative_frame_count=settings.reference_frame_count,
            reference_frame_max_width=settings.reference_frame_max_width,
            moderation_fallback_enabled=settings.art_moderation_fallback_enabled,
            moderation_final_action=settings.art_moderation_final_action,
        )
    logger.info("Completed interactive YouTube workflow job_id=%s", manifest.id)
    print_json(
        {
            "job_id": manifest.id,
            "clips": [str(path) for path in clips],
            "illustrated_videos": [str(path) for path in illustrated],
        }
    )


@app.command("run-e2e")
def run_e2e(
    source: str,
    no_approval: bool = typer.Option(
        False,
        "--no-approval",
        help="Required explicit processing-gate bypass.",
    ),
    vertical: bool = True,
    art: bool = False,
) -> None:
    """Run every processing stage; requires the explicit no-approval flag."""
    if not no_approval:
        raise typer.BadParameter("run-e2e requires --no-approval")

    local_source = Path(source)
    if not local_source.is_file():
        process_youtube(
            source,
            reviewer="CLI --no-approval",
            vertical=vertical,
            art=art,
            no_approval=True,
        )
        return

    settings, store = context()
    candidate = VideoCandidate(
        video_id="local",
        title=local_source.name,
        channel_id="local",
        channel_title="Local",
        risk_reasons=["Local source processed with --no-approval"],
    )
    manifest = store.create(
        JobManifest(
            id=build_job_id(candidate.title, candidate.channel_title),
            source=candidate,
        )
    )
    bypass_processing_gate(store, manifest.id, "ingest")
    ingest_local(store, store.load(manifest.id), local_source)
    pipeline = Pipeline(
        store,
        FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary),
    )
    pipeline.analyze(
        manifest.id,
        AssemblyAITranscriber(settings.require("assemblyai_api_key")),
        GeminiHighlightAnalyzer(
            settings.require("gemini_api_key"),
            model=settings.gemini_model,
        ),
        min_seconds=settings.min_clip_seconds,
        max_seconds=settings.max_clip_seconds,
        seconds_per_candidate=settings.highlight_seconds_per_candidate,
        min_candidates=settings.highlight_min_candidates,
        max_candidates=settings.highlight_max_candidates,
    )
    bypass_processing_gate(store, manifest.id, "spans")
    clips = pipeline.render_clips(
        manifest.id,
        RenderOptions(vertical=vertical),
    )
    illustrated: list[Path] = []
    if art:
        illustrated = pipeline.render_art(
            manifest.id,
            OpenAIImageProvider(
                settings.require("openai_api_key"),
                model=settings.openai_image_model,
                input_fidelity=settings.openai_image_fidelity,
            ),
            GeminiSceneAnalyzer(
                settings.require("gemini_api_key"),
                settings.scene_context_model,
            ),
            style="cinematic editorial comic, clean ink, rich color, no text",
            seconds_per_panel=settings.art_seconds_per_panel,
            min_panels=settings.art_min_panels,
            max_panels=settings.art_max_panels,
            subpanels_per_image=settings.art_subpanels_per_image,
            representative_frame_count=settings.reference_frame_count,
            reference_frame_max_width=settings.reference_frame_max_width,
            moderation_fallback_enabled=settings.art_moderation_fallback_enabled,
            moderation_final_action=settings.art_moderation_final_action,
        )
    print_json(
        {
            "job_id": manifest.id,
            "clips": [str(path) for path in clips],
            "illustrated_videos": [str(path) for path in illustrated],
        }
    )


@app.command("create-document")
def create_document(title: str = "Document") -> None:
    """Create a review job for an authorized document source."""
    _, store = context()
    manifest = store.create(
        JobManifest(
            id=build_job_id(title, "document"),
            source_kind=SourceKind.DOCUMENT,
        )
    )
    typer.echo(manifest.id)


@app.command("ingest-document")
def ingest_document_command(
    job_id: str,
    source: Path,
    no_approval: bool = typer.Option(False, "--no-approval"),
) -> None:
    settings, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "ingest")
    path = ingest_document(
        store,
        store.load(job_id),
        source,
        max_bytes=settings.document_max_bytes,
    )
    typer.echo(path)


@app.command("extract-document")
def extract_document_command(
    job_id: str,
    no_approval: bool = typer.Option(False, "--no-approval"),
) -> None:
    settings, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "ingest")
    document = extract_document(
        store,
        store.load(job_id),
        GeminiDocumentOCR(
            settings.require("gemini_api_key"),
            settings.document_ocr_model,
        ),
        max_pages=settings.document_max_pages,
        embedded_text_min_chars=settings.document_ocr_min_chars,
    )
    print_json(document.model_dump(mode="json"))


@app.command("analyze-document")
def analyze_document_command(
    job_id: str,
    mode: ScriptMode = ScriptMode.FAITHFUL,
    no_approval: bool = typer.Option(False, "--no-approval"),
    content_start_page: int | None = typer.Option(
        None,
        "--content-start-page",
        min=1,
        help="One-based page where core narrated content begins.",
    ),
) -> None:
    settings, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "ingest")
    pipeline = Pipeline(store, FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary))
    preflight = pipeline.analyze_document(
        job_id,
        ocr=GeminiDocumentOCR(
            settings.require("gemini_api_key"),
            settings.document_ocr_model,
        ),
        script_analyzer=GeminiDocumentScriptAnalyzer(
            settings.require("gemini_api_key"),
            settings.gemini_model,
        )
        if mode == ScriptMode.ADAPTED
        else None,
        story_analyzer=GeminiStoryAnalyzer(
            settings.require("gemini_api_key"),
            settings.scene_context_model,
        ),
        mode=mode,
        front_matter_analyzer=(
            GeminiFrontMatterAnalyzer(
                settings.require("gemini_api_key"),
                settings.gemini_model,
            )
            if content_start_page is not None
            else None
        ),
        content_start_page=content_start_page,
        front_matter_max_chars=settings.document_front_matter_max_chars,
        target_section_words=settings.document_target_section_words,
        max_section_words=settings.document_max_section_words,
        max_pages=settings.document_max_pages,
        embedded_text_min_chars=settings.document_ocr_min_chars,
    )
    print_json(preflight)


@app.command("synthesize-narration")
def synthesize_narration_command(
    job_id: str,
    confirm_cost: bool = typer.Option(False, "--confirm-cost"),
    no_approval: bool = typer.Option(False, "--no-approval"),
) -> None:
    settings, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "spans")
    pipeline = Pipeline(store, FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary))
    preflight = pipeline.document_preflight(
        job_id,
        words_per_panel=settings.document_words_per_panel,
        subpanels_per_image=settings.art_subpanels_per_image,
    )
    print_json(preflight)
    if preflight["narration_characters"] > settings.document_max_narration_characters:
        raise typer.BadParameter("Narration exceeds configured character limit")
    if not confirm_cost:
        raise typer.BadParameter("Paid narration requires --confirm-cost")
    narration = pipeline.synthesize_document_narration(
        job_id,
        ElevenLabsNarrationProvider(
            settings.require("elevenlabs_api_key"),
            model=settings.elevenlabs_model,
            output_format=settings.elevenlabs_output_format,
            stability=settings.elevenlabs_stability,
            similarity_boost=settings.elevenlabs_similarity_boost,
            style=settings.elevenlabs_style,
        ),
        voice_ids=settings.elevenlabs_voice_ids,
        pause_seconds=settings.narration_pause_seconds,
        audio_lufs=settings.narration_audio_lufs,
    )
    print_json(narration.model_dump(mode="json"))


@app.command("render-document")
def render_document_command(
    job_id: str,
    style: str = "cinematic editorial comic, clean ink, rich color, no text",
    confirm_cost: bool = typer.Option(False, "--confirm-cost"),
    no_approval: bool = typer.Option(False, "--no-approval"),
) -> None:
    settings, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "spans")
    if not confirm_cost:
        raise typer.BadParameter("Paid image rendering requires --confirm-cost")
    pipeline = Pipeline(store, FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary))
    output = pipeline.render_document(
        job_id,
        OpenAIImageProvider(
            settings.require("openai_api_key"),
            model=settings.openai_image_model,
            input_fidelity=settings.openai_image_fidelity,
        ),
        GeminiStoryAnalyzer(
            settings.require("gemini_api_key"),
            settings.scene_context_model,
        ),
        style=style,
        words_per_panel=settings.document_words_per_panel,
        min_panels=settings.document_min_panels,
        max_panels=settings.document_max_panels,
        subpanels_per_image=settings.art_subpanels_per_image,
        moderation_fallback_enabled=settings.art_moderation_fallback_enabled,
        moderation_final_action=settings.art_moderation_final_action,
    )
    typer.echo(output)


@app.command("process-document")
def process_document(
    source: Path,
    mode: ScriptMode = ScriptMode.FAITHFUL,
    no_approval: bool = typer.Option(False, "--no-approval"),
    confirm_cost: bool = typer.Option(False, "--confirm-cost"),
    content_start_page: int | None = typer.Option(
        None,
        "--content-start-page",
        min=1,
        help="One-based page where core narrated content begins.",
    ),
) -> None:
    if not no_approval:
        raise typer.BadParameter("process-document requires --no-approval")
    if not confirm_cost:
        raise typer.BadParameter("process-document requires --confirm-cost")
    settings, store = context()
    manifest = store.create(
        JobManifest(
            id=build_job_id(source.stem, "document"),
            source_kind=SourceKind.DOCUMENT,
        )
    )
    bypass_processing_gate(store, manifest.id, "ingest")
    ingest_document(
        store,
        store.load(manifest.id),
        source,
        max_bytes=settings.document_max_bytes,
    )
    pipeline = Pipeline(store, FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary))
    pipeline.analyze_document(
        manifest.id,
        ocr=GeminiDocumentOCR(
            settings.require("gemini_api_key"),
            settings.document_ocr_model,
        ),
        script_analyzer=GeminiDocumentScriptAnalyzer(
            settings.require("gemini_api_key"),
            settings.gemini_model,
        )
        if mode == ScriptMode.ADAPTED
        else None,
        story_analyzer=GeminiStoryAnalyzer(
            settings.require("gemini_api_key"),
            settings.scene_context_model,
        ),
        mode=mode,
        front_matter_analyzer=(
            GeminiFrontMatterAnalyzer(
                settings.require("gemini_api_key"),
                settings.gemini_model,
            )
            if content_start_page is not None
            else None
        ),
        content_start_page=content_start_page,
        front_matter_max_chars=settings.document_front_matter_max_chars,
        target_section_words=settings.document_target_section_words,
        max_section_words=settings.document_max_section_words,
        max_pages=settings.document_max_pages,
        embedded_text_min_chars=settings.document_ocr_min_chars,
    )
    bypass_processing_gate(store, manifest.id, "spans")
    preflight = pipeline.document_preflight(
        manifest.id,
        words_per_panel=settings.document_words_per_panel,
        subpanels_per_image=settings.art_subpanels_per_image,
    )
    print_json(preflight)
    if preflight["narration_characters"] > settings.document_max_narration_characters:
        raise typer.BadParameter("Narration exceeds configured character limit")
    pipeline.synthesize_document_narration(
        manifest.id,
        ElevenLabsNarrationProvider(
            settings.require("elevenlabs_api_key"),
            model=settings.elevenlabs_model,
            output_format=settings.elevenlabs_output_format,
            stability=settings.elevenlabs_stability,
            similarity_boost=settings.elevenlabs_similarity_boost,
            style=settings.elevenlabs_style,
        ),
        voice_ids=settings.elevenlabs_voice_ids,
        pause_seconds=settings.narration_pause_seconds,
        audio_lufs=settings.narration_audio_lufs,
    )
    output = pipeline.render_document(
        manifest.id,
        OpenAIImageProvider(
            settings.require("openai_api_key"),
            model=settings.openai_image_model,
            input_fidelity=settings.openai_image_fidelity,
        ),
        GeminiStoryAnalyzer(
            settings.require("gemini_api_key"),
            settings.scene_context_model,
        ),
        style="cinematic editorial comic, clean ink, rich color, no text",
        words_per_panel=settings.document_words_per_panel,
        min_panels=settings.document_min_panels,
        max_panels=settings.document_max_panels,
        subpanels_per_image=settings.art_subpanels_per_image,
        moderation_fallback_enabled=settings.art_moderation_fallback_enabled,
        moderation_final_action=settings.art_moderation_final_action,
    )
    print_json({"job_id": manifest.id, "output": str(output)})


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
def ingest_local_command(
    job_id: str,
    source: Path,
    no_approval: bool = typer.Option(False, "--no-approval"),
) -> None:
    _, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "ingest")
    typer.echo(ingest_local(store, store.load(job_id), source))


@app.command("download-youtube")
def download_youtube_command(
    job_id: str,
    url: str,
    no_approval: bool = typer.Option(False, "--no-approval"),
) -> None:
    """Download only a source whose authorization is documented in the job."""
    _, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "ingest")
    typer.echo(download_youtube(store, store.load(job_id), url))


@app.command()
def analyze(
    job_id: str,
    no_approval: bool = typer.Option(False, "--no-approval"),
) -> None:
    settings, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "ingest")
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
        seconds_per_candidate=settings.highlight_seconds_per_candidate,
        min_candidates=settings.highlight_min_candidates,
        max_candidates=settings.highlight_max_candidates,
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
    no_approval: bool = typer.Option(False, "--no-approval"),
) -> None:
    settings, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "spans")
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
    no_approval: bool = typer.Option(False, "--no-approval"),
    segment_id: Annotated[list[str] | None, typer.Option("--segment-id")] = None,
) -> None:
    settings, store = context()
    if no_approval:
        bypass_processing_gate(store, job_id, "spans")
    pipeline = Pipeline(store, FFmpeg(settings.ffmpeg_binary, settings.ffprobe_binary))
    outputs = pipeline.render_art(
        job_id,
        OpenAIImageProvider(
            settings.require("openai_api_key"),
            model=settings.openai_image_model,
            input_fidelity=settings.openai_image_fidelity,
        ),
        GeminiSceneAnalyzer(
            settings.require("gemini_api_key"),
            settings.scene_context_model,
        ),
        style=style,
        seconds_per_panel=settings.art_seconds_per_panel,
        min_panels=settings.art_min_panels,
        max_panels=settings.art_max_panels,
        subpanels_per_image=settings.art_subpanels_per_image,
        representative_frame_count=settings.reference_frame_count,
        reference_frame_max_width=settings.reference_frame_max_width,
        segment_ids=set(segment_id) if segment_id else None,
        moderation_fallback_enabled=settings.art_moderation_fallback_enabled,
        moderation_final_action=settings.art_moderation_final_action,
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
