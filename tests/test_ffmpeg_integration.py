import shutil
import subprocess
from pathlib import Path

import pytest

from clipsnstrips.media.ffmpeg import FFmpeg
from clipsnstrips.models import RenderOptions, Segment


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_render_tiny_generated_clip(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    destination = tmp_path / "clip.mp4"
    FFmpeg().render_clip(
        source,
        destination,
        Segment(start=0, end=1, hook="Test", confidence=1),
        RenderOptions(),
    )
    assert destination.stat().st_size > 0
