import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from clipsnstrips.youtube.categories import category_map, resolve_category
from clipsnstrips.youtube.discovery import YouTubeDiscovery


def test_category_names_and_ids_are_resolved() -> None:
    assert resolve_category("COMEDY") == "23"
    assert resolve_category("Comedy") == "23"
    assert resolve_category("news & politics") == "25"
    assert resolve_category("23") == "23"
    assert category_map()["Science & Technology"] == "28"


def test_unknown_category_lists_valid_names() -> None:
    with pytest.raises(ValueError, match="Available categories"):
        resolve_category("not-a-category")


def test_youtube_errors_do_not_expose_api_key() -> None:
    response = httplib2.Response({"status": "400"})
    content = json.dumps({"error": {"message": "The requested chart is unavailable"}}).encode()
    error = HttpError(
        response,
        content,
        uri="https://youtube.googleapis.com/youtube/v3/videos?key=SECRET_KEY",
    )

    class FailedRequest:
        def execute(self) -> None:
            raise error

    with pytest.raises(RuntimeError) as caught:
        YouTubeDiscovery._execute(FailedRequest(), operation="test")

    assert "SECRET_KEY" not in str(caught.value)
    assert "requested chart is unavailable" in str(caught.value)
