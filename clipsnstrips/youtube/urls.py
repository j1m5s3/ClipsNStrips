from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def video_id_from_url(value: str) -> str:
    """Extract a YouTube video ID from common URL formats or accept a bare ID."""
    candidate = value.strip()
    if not candidate:
        raise ValueError("YouTube URL or video ID is required")
    if "://" not in candidate and "/" not in candidate:
        return candidate

    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    host = (parsed.hostname or "").casefold()
    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
    }:
        if parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            video_id = (
                parts[1] if len(parts) >= 2 and parts[0] in {"embed", "live", "shorts"} else ""
            )
    else:
        raise ValueError(f"Unsupported YouTube host: {host or 'missing'}")

    if not video_id:
        raise ValueError(f"Could not extract a video ID from: {value}")
    return video_id
