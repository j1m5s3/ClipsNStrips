from __future__ import annotations

from pathlib import Path

import assemblyai as aai

from clipsnstrips.models import Transcript, Word


class AssemblyAITranscriber:
    def __init__(self, api_key: str) -> None:
        aai.settings.api_key = api_key

    def transcribe(self, media: Path) -> Transcript:
        config = aai.TranscriptionConfig(
            speaker_labels=True,
            punctuate=True,
            format_text=True,
        )
        result = aai.Transcriber().transcribe(str(media), config=config)
        if result.status == aai.TranscriptStatus.error:
            raise RuntimeError(f"AssemblyAI transcription failed: {result.error}")
        words = [
            Word(
                text=word.text,
                start_ms=word.start,
                end_ms=word.end,
                speaker=getattr(word, "speaker", None),
            )
            for word in (result.words or [])
        ]
        return Transcript(
            text=result.text or "",
            words=words,
            provider_id=result.id,
        )
