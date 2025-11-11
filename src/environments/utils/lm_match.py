import json
import logging
from typing import Any
from pydantic import BaseModel, Field
from playwright.async_api import Request

from utils.oai import openai_structured_output_request_async

logger = logging.getLogger(__name__)


class ResponseFormat(BaseModel):
    selected_match: int = Field(description="The index of the selected match")
    reasoning: str = Field(description="The reasoning for the selected match")
    confidence: float = Field(description="The confidence score for the selected match")


# TODO: sometimes selector returns JSON instead of website contents, this should be handled.


def _serialize_request(request: Request | dict[str, Any]) -> dict[str, Any]:
    """Convert a Playwright Request object to a JSON-serializable dict.

    If already a dict, return as-is.
    """
    if isinstance(request, dict):
        return request

    # Extract serializable properties from Playwright Request
    serialized = {
        "method": request.method,
        "url": request.url,
        "headers": dict(request.headers),
        "resourceType": request.resource_type,
    }

    # Add post data if available
    try:
        post_data = request.post_data
        if post_data:
            serialized["postData"] = post_data
    except Exception:
        pass

    return serialized


def _get_request_string(i: int | None, request: Request | dict[str, Any]) -> str:
    serialized = _serialize_request(request)
    method = serialized.get("method", "")
    url = serialized.get("url", "")
    resource_type = serialized.get("resourceType", "")
    headers = serialized.get("headers", "")
    post_data = serialized.get("postData", "")

    candidate_str = (
        f"{str(i) if i is not None else ''} {method} {url} ({resource_type})"
    )
    candidate_str += f"\n- headers:\n{json.dumps(headers, indent=2)}"
    if post_data:
        candidate_str += f"\n- post data:\n{post_data}"

    return candidate_str


async def retrieve_best_request_match(
    target_request: Request | dict[str, Any],
    candidates: list[dict[str, Any]],
) -> int:
    if not candidates:
        raise ValueError("candidates list cannot be empty")

    # Serialize the request if it's a Playwright Request object
    candidate_strings = []
    for i, candidate in enumerate(candidates):
        candidate_strings.append(_get_request_string(i, candidate))

    candidates_str = "\n\n".join(candidate_strings)
    request_str = _get_request_string(None, target_request)

    try:
        # Call OpenAI API with structured outputs using centralized utility
        result = await openai_structured_output_request_async(
            prompt_name="lm_match",
            model="gpt-5-nano",
            reasoning="minimal",
            text_format=ResponseFormat,
            auto_set_experiment=True,
            request=request_str,
            candidates=candidates_str,
        )

        selected_idx = result.selected_match

        # Validate index is within bounds
        if selected_idx < 0 or selected_idx >= len(candidates):
            logger.warning(
                "LLM returned invalid index %d (candidates: %d). Falling back to first candidate.",
                selected_idx,
                len(candidates),
            )
            return 0

        return selected_idx

    except Exception as e:
        # For errors, log and fall back
        logger.error(
            "Error calling OpenAI API: %s. Falling back to first candidate.", e
        )
        return 0
