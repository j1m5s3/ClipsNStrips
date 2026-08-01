from __future__ import annotations

import base64
import logging
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from openai import BadRequestError, OpenAI

from clipsnstrips.art.prompts import PanelPrompt

logger = logging.getLogger(__name__)


@dataclass
class ImageModerationBlocked(RuntimeError):
    categories: list[str]
    stage: str | None = None
    request_id: str | None = None
    code: str = "moderation_blocked"
    fallback_attempts: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, "Image request blocked by provider moderation")

    def audit_metadata(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "categories": self.categories,
            "stage": self.stage,
            "request_id": self.request_id,
        }


class ImageProvider(Protocol):
    @property
    def cache_key(self) -> str: ...

    def generate(
        self,
        prompt: PanelPrompt,
        destination: Path,
        *,
        reference_images: list[Path] | None = None,
        previous_panel: Path | None = None,
    ) -> dict[str, Any]: ...


class OpenAIImageProvider:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-image-1",
        size: str = "1024x1536",
        input_fidelity: str = "high",
    ) -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.size = size
        self.input_fidelity = input_fidelity

    @property
    def cache_key(self) -> str:
        return f"openai:{self.model}:{self.size}:{self.input_fidelity}:reference-v2"

    def generate(
        self,
        prompt: PanelPrompt,
        destination: Path,
        *,
        reference_images: list[Path] | None = None,
        previous_panel: Path | None = None,
    ) -> dict[str, Any]:
        references = self._valid_references(reference_images or [], previous_panel)
        logger.info(
            "Starting image generation model=%s panel=%d destination=%s references=%d",
            self.model,
            prompt.index,
            destination,
            len(references),
        )
        try:
            if references:
                with ExitStack() as stack:
                    files = [stack.enter_context(path.open("rb")) for path in references]
                    response = self.client.images.edit(
                        model=self.model,
                        image=files,
                        prompt=(
                            "Use the supplied source frames as factual visual references for "
                            "subject likeness, clothing, roles, composition, and environment. "
                            "Do not merge distinct people or add facial hair, accessories, or "
                            "age cues that are absent from the references. "
                            + (
                                "The final supplied image is the previous illustrated panel; "
                                "match its comic rendering style while keeping source likeness. "
                                if previous_panel is not None
                                else ""
                            )
                            + prompt.prompt
                        ),
                        size=self.size,
                        input_fidelity=self.input_fidelity,
                    )
                generation_mode = "reference_edit"
            else:
                logger.warning(
                    "No valid references; using text-only generation panel=%d",
                    prompt.index,
                )
                response = self.client.images.generate(
                    model=self.model,
                    prompt=prompt.prompt,
                    size=self.size,
                )
                generation_mode = "text_fallback"
        except BadRequestError as error:
            moderation_error = self._moderation_error(error)
            if moderation_error is None:
                raise
            logger.warning(
                "Image request blocked by moderation panel=%d categories=%s request_id=%s",
                prompt.index,
                ",".join(moderation_error.categories) or "unspecified",
                moderation_error.request_id or "unavailable",
            )
            raise moderation_error from None
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
            "mode": generation_mode,
            "reference_count": len(references),
            "input_fidelity": self.input_fidelity,
            "provider_id": getattr(image, "id", "") or "",
            "revised_prompt": image.revised_prompt or "",
        }

    @staticmethod
    def _valid_references(
        reference_images: list[Path],
        previous_panel: Path | None,
    ) -> list[Path]:
        candidates = [*reference_images]
        if previous_panel is not None:
            candidates.append(previous_panel)
        references: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen or not resolved.is_file():
                continue
            seen.add(resolved)
            references.append(resolved)
        return references

    @staticmethod
    def _moderation_error(
        error: BadRequestError,
    ) -> ImageModerationBlocked | None:
        body = error.body if isinstance(error.body, dict) else {}
        details = body.get("error", body)
        if not isinstance(details, dict) or details.get("code") != "moderation_blocked":
            return None
        moderation = details.get("moderation_details", {})
        if not isinstance(moderation, dict):
            moderation = {}
        categories = moderation.get("categories", [])
        if not isinstance(categories, list):
            categories = []
        return ImageModerationBlocked(
            categories=[str(category) for category in categories],
            stage=str(moderation["moderation_stage"])
            if moderation.get("moderation_stage")
            else None,
            request_id=getattr(error, "request_id", None),
            code=str(details.get("code", "moderation_blocked")),
        )


class MidjourneyProvider:
    """Reserved for a future official Midjourney API implementation."""

    @property
    def cache_key(self) -> str:
        return "midjourney:unavailable"

    def generate(
        self,
        prompt: PanelPrompt,
        destination: Path,
        *,
        reference_images: list[Path] | None = None,
        previous_panel: Path | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "Midjourney has no generally available official API; use a supported provider"
        )
