from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from clipsnstrips.models import JobManifest, utc_now

logger = logging.getLogger(__name__)


def build_job_id(
    video_title: str,
    channel_title: str,
    *,
    timestamp: datetime | None = None,
    max_component_length: int = 48,
) -> str:
    moment = timestamp or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    video = _condense_component(
        video_title,
        fallback="video",
        max_length=max_component_length,
    )
    channel = _condense_component(
        channel_title,
        fallback="channel",
        max_length=max_component_length,
    )
    return f"{video}_{channel}_{stamp}"


def _condense_component(value: str, *, fallback: str, max_length: int) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    condensed = re.sub(r"[^a-z0-9]+", "", ascii_value.casefold())
    return (condensed or fallback)[:max_length]


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def directory(self, job_id: str) -> Path:
        return self.root / job_id

    def manifest_path(self, job_id: str) -> Path:
        return self.directory(job_id) / "manifest.json"

    def create(self, manifest: JobManifest | None = None) -> JobManifest:
        manifest = manifest or JobManifest()
        directory = self.directory(manifest.id)
        for name in ("source", "analysis", "clips", "art", "logs", "uploads"):
            (directory / name).mkdir(parents=True, exist_ok=True)
        self.save(manifest)
        logger.info("Created job job_id=%s directory=%s", manifest.id, directory)
        return manifest

    def load(self, job_id: str) -> JobManifest:
        manifest = JobManifest.model_validate_json(
            self.manifest_path(job_id).read_text(encoding="utf-8")
        )
        logger.debug("Loaded job job_id=%s stage=%s", job_id, manifest.stage)
        return manifest

    def save(self, manifest: JobManifest) -> None:
        manifest.updated_at = utc_now()
        path = self.manifest_path(manifest.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            manifest.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        temporary.replace(path)
        logger.debug("Saved job job_id=%s stage=%s", manifest.id, manifest.stage)

    def write_json(self, job_id: str, relative_path: str, value: object) -> Path:
        path = self.directory(job_id) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        logger.debug("Wrote job JSON job_id=%s path=%s", job_id, relative_path)
        return path


def sha256_file(path: Path) -> str:
    logger.debug("Calculating checksum path=%s", path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
