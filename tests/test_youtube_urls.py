import pytest

from clipsnstrips.youtube.urls import video_id_from_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abc123", "abc123"),
        ("https://www.youtube.com/watch?v=abc123&t=10", "abc123"),
        ("https://youtu.be/abc123?si=value", "abc123"),
        ("https://www.youtube.com/shorts/abc123", "abc123"),
        ("https://www.youtube.com/embed/abc123", "abc123"),
        ("youtube.com/live/abc123", "abc123"),
    ],
)
def test_extracts_common_youtube_urls(value: str, expected: str) -> None:
    assert video_id_from_url(value) == expected


def test_rejects_non_youtube_urls() -> None:
    with pytest.raises(ValueError, match="Unsupported YouTube host"):
        video_id_from_url("https://example.com/watch?v=abc123")
