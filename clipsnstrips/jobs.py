from __future__ import annotations

import hashlib
import json
from pathlib import Path

from clipsnstrips.models import JobManifest, utc_now


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
        return manifest

    def load(self, job_id: str) -> JobManifest:
        return JobManifest.model_validate_json(
            self.manifest_path(job_id).read_text(encoding="utf-8")
        )

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

    def write_json(self, job_id: str, relative_path: str, value: object) -> Path:
        path = self.directory(job_id) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
        return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
