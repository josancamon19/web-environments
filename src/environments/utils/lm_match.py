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


async def retrieve_best_request_match(
    target_request: Request | dict[str, Any],
    candidates: list[dict[str, Any]],
) -> int:
    # Serialize the request if it's a Playwright Request object
    serialized_request = _serialize_request(target_request)
    candidates = [_serialize_request(candidate) for candidate in candidates]
    prompt = prompt_template.format(
        request=json.dumps(serialized_request),
        candidates=json.dumps(candidates, indent=2),
    )

    # Call OpenAI API with structured outputs
    response = await client.responses.parse(
        model="gpt-5-nano",
        reasoning={"effort": "minimal"},
        input=[
            {
                "role": "system",
                "content": "You are a helpful assistant that matches HTTP requests.",
            },
            {"role": "user", "content": prompt},
        ],
        text_format=ResponseFormat,
    )

    result = response.output_parsed
    print(result)
    return result.selected_match
