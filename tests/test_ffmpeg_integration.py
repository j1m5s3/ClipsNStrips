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


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_art_video_maps_panel_images_instead_of_source_video(tmp_path: Path) -> None:
    source = tmp_path / "blue-source.mp4"
    panel = tmp_path / "red-panel.png"
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
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240",
            "-frames:v",
            "1",
            str(panel),
        ],
        check=True,
        capture_output=True,
    )

    destination = tmp_path / "panel-video.mp4"
    FFmpeg().compose_art_video(
        [panel, panel],
        [0.5, 0.5],
        source,
        0,
        destination,
        width=320,
        height=240,
    )
    frame = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "0.25",
            "-i",
            str(destination),
            "-vf",
            "scale=1:1",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    red, green, blue = frame[:3]
    assert red > green + 50
    assert red > blue + 50


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_extract_frame_uses_requested_timestamp(tmp_path: Path) -> None:
    source = tmp_path / "changing-source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    red_frame = FFmpeg().extract_frame(source, tmp_path / "red.jpg", 0.25)
    blue_frame = FFmpeg().extract_frame(source, tmp_path / "blue.jpg", 1.25)

    def pixel(path: Path) -> tuple[int, int, int]:
        data = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-vf",
                "scale=1:1",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            check=True,
            capture_output=True,
        ).stdout
        return data[0], data[1], data[2]

    red = pixel(red_frame)
    blue = pixel(blue_frame)
    assert red[0] > red[2] + 50
    assert blue[2] > blue[0] + 50


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is not installed")
def test_create_neutral_placeholder_image(tmp_path: Path) -> None:
    destination = FFmpeg().create_placeholder_image(tmp_path / "placeholder.png")
    assert destination.is_file()
    assert destination.stat().st_size > 0
