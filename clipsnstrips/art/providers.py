from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Protocol

from openai import OpenAI

from clipsnstrips.art.prompts import PanelPrompt

logger = logging.getLogger(__name__)


class ImageProvider(Protocol):
    def generate(self, prompt: PanelPrompt, destination: Path) -> dict[str, str]: ...


class OpenAIImageProvider:
    def __init__(self, api_key: str, model: str = "gpt-image-1") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def generate(self, prompt: PanelPrompt, destination: Path) -> dict[str, str]:
        logger.info(
            "Starting image generation model=%s panel=%d destination=%s",
            self.model,
            prompt.index,
            destination,
        )
        response = self.client.images.generate(
            model=self.model,
            prompt=prompt.prompt,
            size="1024x1536",
        )
        image = response.data[0]
        if image.b64_json:
            content = base64.b64decode(image.b64_json)
        elif image.url:
            import urllib.request

            with urllib.request.urlopen(image.url, timeout=60) as result:
                content = result.read()
        else:
            raise RuntimeError("Image provider returned no image")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        logger.info(
            "Completed image generation model=%s panel=%d bytes=%d",
            self.model,
            prompt.index,
            len(content),
        )
        return {
            "provider": "openai",
            "model": self.model,
            "provider_id": getattr(image, "id", "") or "",
            "revised_prompt": image.revised_prompt or "",
        }


class MidjourneyProvider:
    """Reserved for a future official Midjourney API implementation."""

    def generate(self, prompt: PanelPrompt, destination: Path) -> dict[str, str]:
        raise NotImplementedError(
            "Midjourney has no generally available official API; use a supported provider"
        )
