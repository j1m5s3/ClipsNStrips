from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from clipsnstrips.models import JobManifest, Stage, UploadRecord

logger = logging.getLogger(__name__)


class YouTubeUploader:
    def __init__(self, credentials: Credentials) -> None:
        self.client = build("youtube", "v3", credentials=credentials, cache_discovery=False)

    def upload_private(
        self,
        manifest: JobManifest,
        artifact: Path,
        job_root: Path,
        *,
        title: str,
        description: str,
        tags: list[str] | None = None,
        category_id: str = "22",
        contains_synthetic_media: bool = True,
    ) -> UploadRecord:
        logger.info(
            "Starting private YouTube upload job_id=%s artifact=%s",
            manifest.id,
            artifact,
        )
        manifest.require_approval("rights")
        manifest.require_approval("output")
        if not artifact.exists():
            raise FileNotFoundError(artifact)
        try:
            relative = artifact.resolve().relative_to(job_root.resolve()).as_posix()
        except ValueError as error:
            raise PermissionError("Upload artifact must be inside this job folder") from error
        allowed = {
            item.path for item in manifest.artifacts if item.kind in {"clip", "illustrated_video"}
        }
        if relative not in allowed:
            raise PermissionError("Upload artifact is not a rendered output in this job manifest")

        body: dict[str, Any] = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags or [],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": contains_synthetic_media,
            },
        }
        request = self.client.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(str(artifact), chunksize=-1, resumable=True),
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(
                    "YouTube upload progress job_id=%s progress=%.1f%%",
                    manifest.id,
                    status.progress() * 100,
                )

        record = UploadRecord(
            video_id=response["id"],
            artifact_path=str(artifact),
            privacy_status="private",
        )
        manifest.uploads.append(record)
        manifest.stage = Stage.UPLOADED_PRIVATE
        logger.info(
            "Completed private YouTube upload job_id=%s video_id=%s",
            manifest.id,
            record.video_id,
        )
        return record

    def publish(self, manifest: JobManifest, video_id: str) -> None:
        logger.info("Starting YouTube publish job_id=%s video_id=%s", manifest.id, video_id)
        manifest.require_approval("publish")
        if not any(item.video_id == video_id for item in manifest.uploads):
            raise PermissionError("Video was not uploaded by this job")
        self.client.videos().update(
            part="status",
            body={"id": video_id, "status": {"privacyStatus": "public"}},
        ).execute()
        for item in manifest.uploads:
            if item.video_id == video_id:
                item.privacy_status = "public"
        manifest.stage = Stage.PUBLISHED
        logger.info("Completed YouTube publish job_id=%s video_id=%s", manifest.id, video_id)
