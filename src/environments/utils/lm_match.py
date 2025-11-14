import json
import logging
from typing import Any
from pydantic import BaseModel, Field
from rebrowser_playwright.async_api import Request

from utils.oai import openai_structured_output_request_async
from utils.normalize_url import normalize_url_for_matching

logger = logging.getLogger(__name__)


class ResponseFormat(BaseModel):
    selected_match: int = Field(description="The index of the selected match")
    reasoning: str = Field(description="The reasoning for the selected match")
    confidence: float = Field(description="The confidence score for the selected match")


def _serialize_request(request: Request | dict[str, Any]) -> dict[str, Any]:
    """Convert a Playwright Request object to a JSON-serializable dict.

    If already a dict, return as-is.
    """
    if isinstance(request, dict):
        return request

    # Extract serializable properties from Playwright Request
    serialized = {
        "method": request.method,
        "url": normalize_url_for_matching(request.url),
        "headers": dict(request.headers),
        "resourceType": request.resource_type,
        "isNavigationRequest": request.is_navigation_request(),
    }

    # Add post data if available
    try:
        post_data = request.post_data
        if post_data:
            serialized["postData"] = post_data
    except Exception:
        pass

    return serialized


def _get_request_string(i: int | None, request: dict[str, Any]) -> str:
    resource_type = request.get("resourceType")  # applies to target
    is_navigation_request = request.get("isNavigationRequest")  # applies to target
    response_mime_type = request.get("responseMimeType")  # applies to candidates

    candidate_str = (
        f"{str(i) + ' ' if i is not None else ''}{request['method']} {request['url']}"
    )

    if resource_type:
        candidate_str += f"\n- resource type: ({resource_type})"
    if is_navigation_request is not None:
        candidate_str += f"\n- is navigation request: {is_navigation_request}"
    if response_mime_type:
        candidate_str += f"\n- response MIME type: {response_mime_type}"

    if headers := request.get("headers"):
        candidate_str += f"\n- headers: ```{json.dumps(headers, ensure_ascii=False)}```"

    if post_data := request.get("postData"):
        candidate_str += (
            f"\n- post data: ```{json.dumps(post_data, ensure_ascii=False)}```"
        )

    return candidate_str


async def retrieve_best_request_match(
    target_request: Request,
    candidates: list[dict[str, Any]],
    metadata: dict[str, Any] = None,
) -> int:
    if not candidates:
        raise ValueError("candidates list cannot be empty")

    # Serialize the request if it's a Playwright Request object
    candidate_strings = [_get_request_string(i, c) for i, c in enumerate(candidates)]
    candidates_str = "\n\n".join(candidate_strings)
    request_str = _get_request_string(None, _serialize_request(target_request))

    try:
        # Call OpenAI API with structured outputs using centralized utility
        result, resp_id = await openai_structured_output_request_async(
            prompt_name="lm_match",
            model="gpt-5-nano",
            reasoning="low",
            text_format=ResponseFormat,
            metadata=metadata,
            request=request_str,
            candidates=candidates_str,
        )

        selected_idx = result.selected_match
        if selected_idx != 0:
            logger.info(
                "Relevant LM match response details: https://platform.openai.com/logs/%s",
                resp_id,
            )

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
