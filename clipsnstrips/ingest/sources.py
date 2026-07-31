from __future__ import annotations

import shutil
from pathlib import Path

from clipsnstrips.jobs import JobStore, sha256_file
from clipsnstrips.models import Artifact, JobManifest, Stage


def ingest_local(
    store: JobStore,
    manifest: JobManifest,
    source: Path,
) -> Path:
    manifest.require_approval("ingest")
    if not source.is_file():
        raise FileNotFoundError(source)
    extension = source.suffix.lower() or ".media"
    destination = store.directory(manifest.id) / "source" / f"original{extension}"
    shutil.copy2(source, destination)
    return _record_source(store, manifest, destination)


def download_youtube(
    store: JobStore,
    manifest: JobManifest,
    url: str,
) -> Path:
    """Download only after a reviewer records explicit ingest authorization."""
    manifest.require_approval("ingest")
    approval = manifest.approval_for("ingest")
    if not approval or not approval.notes.strip():
        raise PermissionError("Ingest approval must document the authorization basis")

    try:
        import yt_dlp
    except ImportError as error:
        raise RuntimeError("Install the yt-dlp project dependency before downloading") from error

    output_template = str(store.directory(manifest.id) / "source" / "original.%(ext)s")
    options = {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        prepared = Path(downloader.prepare_filename(info))
    mp4 = prepared.with_suffix(".mp4")
    destination = mp4 if mp4.exists() else prepared
    if not destination.exists():
        matches = list((store.directory(manifest.id) / "source").glob("original.*"))
        if not matches:
            raise RuntimeError("Downloader did not produce a media file")
        destination = matches[0]
    return _record_source(store, manifest, destination)


def _record_source(store: JobStore, manifest: JobManifest, source: Path) -> Path:
    relative = source.relative_to(store.directory(manifest.id)).as_posix()
    checksum = sha256_file(source)
    manifest.source_path = relative
    manifest.source_checksum = checksum
    manifest.add_artifact(Artifact(kind="source", path=relative, checksum=checksum))
    manifest.stage = Stage.INGESTED
    store.save(manifest)
    return source
