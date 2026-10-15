"""Shared Responses transport: credentials, page evidence and strict parsing only."""

import base64
import json
import os
from typing import Literal

from openai import APIError, APIResponseValidationError, OpenAI
from pydantic import BaseModel, ValidationError

from .models import PageDiscoveryInput

DEFAULT_MODEL = "gpt-6.1-sol"


class OpenAIAnalyzerConfigurationError(ValueError):
    pass


class OpenAIAnalyzerAPIError(RuntimeError):
    pass


class InvalidDiscoveryResponseError(RuntimeError):
    pass


class OpenAIPageClient:
    def __init__(self, *, model: str | None = None, image_detail: Literal["high", "original"] = "high", client_factory=OpenAI) -> None:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise OpenAIAnalyzerConfigurationError("OPENAI_API_KEY is required")
        self.model = (model if model is not None else os.environ.get("SNG_DISCOVERY_MODEL", DEFAULT_MODEL)).strip()
        if not self.model:
            raise OpenAIAnalyzerConfigurationError("SNG_DISCOVERY_MODEL must not be empty")
        if image_detail not in {"high", "original"}:
            raise OpenAIAnalyzerConfigurationError("image_detail must be high or original")
        self.image_detail = image_detail
        # No hidden retry/pass or key persistence; each page makes one SDK call.
        self.client = client_factory(api_key=api_key, max_retries=0, timeout=180.0)

    def close(self) -> None:
        self.client.close()

    def parse_page(self, page: PageDiscoveryInput, *, instructions: str, text_format: type[BaseModel],
                   extra_evidence: dict | None = None, response_error=InvalidDiscoveryResponseError):
        page = PageDiscoveryInput.model_validate(page)
        image = base64.b64encode(page.image_path.read_bytes()).decode("ascii")
        native = [[span.text, [round(span.bbox.x0, 3), round(span.bbox.y0, 3),
                               round(span.bbox.x1, 3), round(span.bbox.y1, 3)]]
                  for span in page.native_text_spans]
        evidence = json.dumps({"render_size": [page.width_px, page.height_px],
                               "native_text_spans": native, **(extra_evidence or {})}, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False)
        try:
            response = self.client.responses.parse(
                model=self.model, instructions=instructions, store=False,
                input=[{"role": "user", "content": [
                    {"type": "input_image", "image_url": f"data:image/webp;base64,{image}",
                     "detail": self.image_detail},
                    {"type": "input_text", "text": evidence},
                ]}], text_format=text_format,
            )
        except (ValidationError, APIResponseValidationError) as exc:
            # Avoid raw SDK bodies/validation inputs, which can contain credentials.
            raise response_error(f"Structured OpenAI response is invalid ({type(exc).__name__})") from None
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            suffix = f", HTTP {status}" if isinstance(status, int) else ""
            raise OpenAIAnalyzerAPIError(f"OpenAI request failed ({type(exc).__name__}{suffix})") from None
        if response.status == "failed":
            raise OpenAIAnalyzerAPIError("OpenAI returned a failed response")
        if response.status != "completed" or response.output_parsed is None:
            raise response_error("OpenAI response was incomplete, refused, or missing structured proposals")
        try:
            parsed = text_format.model_validate(response.output_parsed)
        except ValidationError:
            raise response_error("OpenAI proposals do not satisfy the requested contract") from None
        return parsed
