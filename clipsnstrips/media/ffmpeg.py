from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from clipsnstrips.models import RenderOptions, Segment

logger = logging.getLogger(__name__)


class FFmpeg:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def check(self) -> None:
        logger.info("Checking FFmpeg executables")
        self._run([self.ffmpeg, "-version"])
        self._run([self.ffprobe, "-version"])
        logger.info("FFmpeg executables are available")

    def probe(self, source: Path) -> dict:
        logger.info("Probing media source=%s", source)
        result = self._run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(source),
            ]
        )
        return json.loads(result.stdout)

    def duration(self, source: Path) -> float:
        probe = self.probe(source)
        value = probe.get("format", {}).get("duration")
        if value is None:
            raise ValueError(f"Media has no duration: {source}")
        return float(value)

    def extract_audio(self, source: Path, destination: Path) -> Path:
        logger.info("Extracting audio source=%s destination=%s", source, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                self.ffmpeg,
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(destination),
            ]
        )
        return destination

    def extract_frame(
        self,
        source: Path,
        destination: Path,
        timestamp: float,
        *,
        max_width: int = 1024,
    ) -> Path:
        if timestamp < 0:
            raise ValueError("Frame timestamp cannot be negative")
        destination.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Extracting reference frame source=%s timestamp=%.3f destination=%s",
            source,
            timestamp,
            destination,
        )
        self._run(
            [
                self.ffmpeg,
                "-y",
                "-ss",
                str(timestamp),
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"scale='min({max_width},iw)':-2",
                "-q:v",
                "2",
                str(destination),
            ]
        )
        return destination

    def create_placeholder_image(
        self,
        destination: Path,
        *,
        width: int = 1024,
        height: int = 1536,
        color: str = "0x1f2937",
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Creating neutral placeholder image destination=%s", destination)
        self._run(
            [
                self.ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={width}x{height}",
                "-frames:v",
                "1",
                str(destination),
            ]
        )
        return destination

    def render_clip(
        self,
        source: Path,
        destination: Path,
        segment: Segment,
        options: RenderOptions,
    ) -> Path:
        logger.info(
            "Rendering clip segment_id=%s start=%.3f end=%.3f destination=%s",
            segment.id,
            segment.start,
            segment.end,
            destination,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        filters = [f"loudnorm=I={options.audio_lufs}:TP=-1.5:LRA=11"]
        video_filters: list[str] = []
        if options.vertical:
            video_filters.append(
                f"scale={options.width}:{options.height}:force_original_aspect_ratio=increase,"
                f"crop={options.width}:{options.height}"
            )
        command = [
            self.ffmpeg,
            "-y",
            "-ss",
            str(segment.start),
            "-to",
            str(segment.end),
            "-i",
            str(source),
        ]
        if video_filters:
            command.extend(["-vf", ",".join(video_filters)])
        command.extend(
            [
                "-af",
                ",".join(filters),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(destination),
            ]
        )
        self._run(command)
        return destination

    def compose_art_video(
        self,
        images: list[Path],
        durations: list[float],
        audio_source: Path,
        audio_start: float,
        destination: Path,
        *,
        width: int = 1080,
        height: int = 1920,
    ) -> Path:
        if not images or len(images) != len(durations):
            raise ValueError("Each image requires a duration")
        logger.info(
            "Composing illustrated video image_count=%d duration=%.3f destination=%s",
            len(images),
            sum(durations),
            destination,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        concat_file = destination.with_suffix(".concat.txt")
        lines: list[str] = []
        for image, duration in zip(images, durations, strict=True):
            safe_path = image.resolve().as_posix().replace("'", "'\\''")
            lines.extend([f"file '{safe_path}'", f"duration {duration:.3f}"])
        lines.append(f"file '{images[-1].resolve().as_posix()}'")
        concat_file.write_text("\n".join(lines), encoding="utf-8")
        total_duration = sum(durations)
        try:
            self._run(
                [
                    self.ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-ss",
                    str(audio_start),
                    "-t",
                    str(total_duration),
                    "-i",
                    str(audio_source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-vf",
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ]
            )
        finally:
            concat_file.unlink(missing_ok=True)
        return destination

    def concat_audio(
        self,
        sources: list[Path],
        destination: Path,
        *,
        pause_seconds: float = 0.25,
        audio_lufs: float = -16,
    ) -> Path:
        if not sources:
            raise ValueError("At least one audio source is required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        pause = destination.with_suffix(".pause.wav")
        concat_file = destination.with_suffix(".concat.txt")
        try:
            entries: list[Path] = []
            if pause_seconds > 0 and len(sources) > 1:
                self._run(
                    [
                        self.ffmpeg,
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=44100:cl=mono",
                        "-t",
                        str(pause_seconds),
                        "-c:a",
                        "pcm_s16le",
                        str(pause),
                    ]
                )
            for index, source in enumerate(sources):
                entries.append(source)
                if pause.exists() and index < len(sources) - 1:
                    entries.append(pause)
            lines = []
            for path in entries:
                safe_path = path.resolve().as_posix().replace("'", "'\\''")
                lines.append(f"file '{safe_path}'")
            concat_file.write_text("\n".join(lines), encoding="utf-8")
            self._run(
                [
                    self.ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-af",
                    f"loudnorm=I={audio_lufs}:TP=-1.5:LRA=11",
                    "-ar",
                    "44100",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(destination),
                ]
            )
        finally:
            pause.unlink(missing_ok=True)
            concat_file.unlink(missing_ok=True)
        return destination

    def compose_art_video_with_audio(
        self,
        images: list[Path],
        durations: list[float],
        audio_source: Path,
        destination: Path,
        *,
        width: int = 1080,
        height: int = 1920,
    ) -> Path:
        if not images or len(images) != len(durations):
            raise ValueError("Each image requires a duration")
        destination.parent.mkdir(parents=True, exist_ok=True)
        concat_file = destination.with_suffix(".concat.txt")
        lines: list[str] = []
        for image, duration in zip(images, durations, strict=True):
            safe_path = image.resolve().as_posix().replace("'", "'\\''")
            lines.extend([f"file '{safe_path}'", f"duration {duration:.3f}"])
        lines.append(f"file '{images[-1].resolve().as_posix()}'")
        concat_file.write_text("\n".join(lines), encoding="utf-8")
        try:
            self._run(
                [
                    self.ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-i",
                    str(audio_source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-vf",
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ]
            )
        finally:
            concat_file.unlink(missing_ok=True)
        return destination

    def split_comic_page(
        self,
        source: Path,
        destination_dir: Path,
        *,
        page_index: int,
        cell_count: int,
        width: int = 1024,
        height: int = 1536,
    ) -> list[Path]:
        if not 1 <= cell_count <= 4:
            raise ValueError("cell_count must be between 1 and 4")
        if width % 2 or height % 2:
            raise ValueError("Comic page dimensions must be even")
        destination_dir.mkdir(parents=True, exist_ok=True)
        cell_width = width // 2
        cell_height = height // 2
        outputs: list[Path] = []
        for cell_index in range(cell_count):
            column = cell_index % 2
            row = cell_index // 2
            destination = destination_dir / (f"page-{page_index:03d}-cell-{cell_index + 1}.png")
            filters = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"crop={cell_width}:{cell_height}:"
                f"{column * cell_width}:{row * cell_height},"
                f"scale={width}:{height}"
            )
            self._run(
                [
                    self.ffmpeg,
                    "-y",
                    "-i",
                    str(source),
                    "-vf",
                    filters,
                    "-frames:v",
                    "1",
                    str(destination),
                ]
            )
            outputs.append(destination)
        return outputs

    def concat_videos(self, sources: list[Path], destination: Path) -> Path:
        if not sources:
            raise ValueError("At least one video source is required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        concat_file = destination.with_suffix(".concat.txt")
        concat_file.write_text(
            "\n".join(f"file '{source.resolve().as_posix()}'" for source in sources),
            encoding="utf-8",
        )
        try:
            self._run(
                [
                    self.ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ]
            )
        finally:
            concat_file.unlink(missing_ok=True)
        return destination

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        logger.debug("Running media command command=%s", subprocess.list2cmdline(command))
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            logger.debug("Media command completed executable=%s", command[0])
            return result
        except FileNotFoundError as error:
            logger.exception("Media executable not found executable=%s", command[0])
            raise RuntimeError(f"Required executable not found: {command[0]}") from error
        except subprocess.CalledProcessError as error:
            logger.error(
                "Media command failed executable=%s return_code=%s",
                command[0],
                error.returncode,
            )
            raise RuntimeError(error.stderr.strip() or "FFmpeg command failed") from error
