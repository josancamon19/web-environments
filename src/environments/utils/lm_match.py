import json
import asyncio
import logging
import re
from typing import Any
import os
from pydantic import BaseModel, Field
from openai import AsyncOpenAI, RateLimitError
from playwright.async_api import Request

import mlflow
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

prompt_template = """
You will be given a target request (as a JSON object via json.dumps), and a list of candidate requests (each also a JSON object via json.dumps) that we want to match the target request to.

The current request comes from a browser replaying a human trajectory. The candidates are HAR request entries, collected in a previous HAR capture, that are similar to the target request.

Your task is to identify the best candidate that matches the target request. To do this, compare the various fields in the target request and each candidate request. Give special attention to fields that typically stay consistent (such as HTTP method, path, core headers, etc.), but deprioritize fields known to change frequently (like dynamic URL parameters or POST data values).

Choose the candidate that is the closest logical match, preferring candidates where stable fields align and dynamic/noisy fields account for most of the differences.

Return the index of the selected match.

<target_request>
{request}
</target_request>

<candidates>
{candidates}
</candidates>
"""


class ResponseFormat(BaseModel):
    selected_match: int = Field(description="The index of the selected match")
    reasoning: str = Field(description="The reasoning for the selected match")
    confidence: float = Field(description="The confidence score for the selected match")


# Initialize OpenAI client
client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
mlflow.set_tracking_uri("http://127.0.0.1:5000")
mlflow.set_experiment("lm-match")
mlflow.openai.autolog()

# TODO: sometimes selector returns JSON instead of website contents, this should be handled.
# TODO: collect traces


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
    max_retries: int = 5,
) -> int:
    if not candidates:
        raise ValueError("candidates list cannot be empty")

    # Serialize the request if it's a Playwright Request object
    candidate_strings = []
    for i, candidate in enumerate(candidates):
        candidate_strings.append(_get_request_string(i, candidate))

    candidates_str = "\n\n".join(candidate_strings)
    prompt = prompt_template.format(
        request=_get_request_string(None, target_request), candidates=candidates_str
    )

    # Retry logic with exponential backoff for rate limit errors
    for attempt in range(max_retries):
        try:
            # Call OpenAI API with structured outputs
            response = await client.responses.parse(
                model="gpt-5-nano",
                reasoning={"effort": "minimal"},
                input=[{"role": "user", "content": prompt}],
                text_format=ResponseFormat,
            )

            result = response.output_parsed
            # print(result)
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

        except RateLimitError as e:
            # Extract retry_after from error message if available
            retry_after = 1.0  # Default wait time in seconds
            error_message = str(e)

            # Try to extract retry time from error message
            if "try again in" in error_message:
                try:
                    # Extract time like "5.417s" or "24.709s"
                    match = re.search(r"try again in ([\d.]+)s", error_message)
                    if match:
                        retry_after = float(match.group(1))
                        # Add a small buffer
                        retry_after = min(retry_after + 0.5, 30.0)  # Cap at 30 seconds
                except (ValueError, AttributeError):
                    pass

            if attempt < max_retries - 1:
                # Exponential backoff, but cap at reasonable maximum
                wait_time = min(retry_after * (2**attempt), 60.0)  # Cap at 60 seconds
                logger.warning(
                    "Rate limit error (attempt %d/%d). Retrying after %.2f seconds...",
                    attempt + 1,
                    max_retries,
                    wait_time,
                )
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    "Rate limit error after %d attempts. Falling back to first candidate.",
                    max_retries,
                )
                # Fallback to first candidate when rate limits persist
                return 0

        except Exception as e:
            # For other errors, log and fall back
            logger.error(
                "Error calling OpenAI API: %s. Falling back to first candidate.", e
            )
            return 0

    # Should not reach here, but just in case
    logger.warning("All retries exhausted. Falling back to first candidate.")
    return 0
