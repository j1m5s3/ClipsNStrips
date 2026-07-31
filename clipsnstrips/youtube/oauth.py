from __future__ import annotations

import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
logger = logging.getLogger(__name__)


def youtube_credentials(client_file: Path, token_file: Path) -> Credentials:
    logger.info("Loading YouTube OAuth credentials token_exists=%s", token_file.exists())
    credentials: Credentials | None = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        logger.info("Refreshing YouTube OAuth credentials")
        credentials.refresh(Request())
    elif not credentials or not credentials.valid:
        if not client_file.exists():
            raise FileNotFoundError(f"YouTube OAuth client file not found: {client_file}")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
        logger.info("Starting interactive YouTube OAuth flow")
        credentials = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    logger.info("YouTube OAuth credentials ready token_path=%s", token_file)
    return credentials
