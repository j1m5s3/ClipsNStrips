from __future__ import annotations

import logging
from pathlib import Path

import assemblyai as aai

from clipsnstrips.models import Transcript, Word

logger = logging.getLogger(__name__)


class AssemblyAITranscriber:
    def __init__(self, api_key: str) -> None:
        aai.settings.api_key = api_key

    def transcribe(self, media: Path) -> Transcript:
        logger.info("Starting AssemblyAI transcription media=%s", media)
        config = aai.TranscriptionConfig(
            speaker_labels=True,
            punctuate=True,
            format_text=True,
        )
        result = aai.Transcriber().transcribe(str(media), config=config)
        if result.status == aai.TranscriptStatus.error:
            logger.error("AssemblyAI transcription failed media=%s", media)
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
        transcript = Transcript(
            text=result.text or "",
            words=words,
            provider_id=result.id,
        )
        logger.info(
            "Completed AssemblyAI transcription media=%s word_count=%d provider_id=%s",
            media,
            len(words),
            result.id,
        )
        return transcript
