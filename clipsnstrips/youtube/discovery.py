from __future__ import annotations

from datetime import datetime
from typing import Any

from googleapiclient.discovery import build

from clipsnstrips.models import VideoCandidate
from clipsnstrips.rights import classify_candidate


class YouTubeDiscovery:
    def __init__(self, api_key: str, owned_channel_ids: set[str] | None = None) -> None:
        self.client = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
        self.owned_channel_ids = owned_channel_ids or set()

    def most_popular(
        self,
        *,
        region: str = "US",
        category: str | None = None,
        limit: int = 25,
    ) -> list[VideoCandidate]:
        parameters: dict[str, Any] = {
            "part": "snippet,contentDetails,status,statistics",
            "chart": "mostPopular",
            "regionCode": region,
            "maxResults": min(max(limit, 1), 50),
        }
        if category:
            parameters["videoCategoryId"] = category
        response = self.client.videos().list(**parameters).execute()
        return [self._candidate(item) for item in response.get("items", [])]

    def by_id(self, video_id: str) -> VideoCandidate:
        response = (
            self.client.videos()
            .list(
                part="snippet,contentDetails,status,statistics",
                id=video_id,
            )
            .execute()
        )
        items = response.get("items", [])
        if not items:
            raise LookupError(f"YouTube video not found: {video_id}")
        return self._candidate(items[0])

    def _candidate(self, item: dict[str, Any]) -> VideoCandidate:
        snippet = item.get("snippet", {})
        details = item.get("contentDetails", {})
        status = item.get("status", {})
        statistics = item.get("statistics", {})
        published = snippet.get("publishedAt")
        candidate = VideoCandidate(
            video_id=item["id"],
            title=snippet.get("title", ""),
            channel_id=snippet.get("channelId", ""),
            channel_title=snippet.get("channelTitle", ""),
            description=snippet.get("description", ""),
            duration=details.get("duration"),
            category_id=snippet.get("categoryId"),
            license=status.get("license"),
            embeddable=status.get("embeddable"),
            licensed_content=details.get("licensedContent"),
            view_count=int(statistics.get("viewCount", 0)),
            like_count=int(statistics.get("likeCount", 0)),
            published_at=datetime.fromisoformat(published.replace("Z", "+00:00"))
            if published
            else None,
        )
        return classify_candidate(candidate, self.owned_channel_ids)
