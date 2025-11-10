import json
from typing import Any
import os
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from playwright.async_api import Request

import mlflow

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
) -> int:
    # Serialize the request if it's a Playwright Request object
    candidate_strings = []
    for i, candidate in enumerate(candidates):
        candidate_strings.append(_get_request_string(i, candidate))

    candidates = "\n\n".join(candidate_strings)
    prompt = prompt_template.format(
        request=_get_request_string(None, target_request), candidates=candidates
    )

    # Call OpenAI API with structured outputs
    response = await client.responses.parse(
        model="gpt-5-nano",
        reasoning={"effort": "minimal"},
        input=[{"role": "user", "content": prompt}],
        text_format=ResponseFormat,
    )

    result = response.output_parsed
    print(result)
    return result.selected_match
