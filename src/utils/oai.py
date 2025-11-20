from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI, BaseModel, OpenAI
import os
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    AsyncRetrying,
)

load_dotenv()

# Initialize clients lazily or with empty string to prevent crash on import
# if OPENAI_API_KEY is not in environment
api_key = os.environ.get("OPENAI_API_KEY") or "dummy_key_for_initialization"

client = OpenAI(api_key=api_key)
async_client: Any = AsyncOpenAI(api_key=api_key)


PROMPTS_DIR = Path("src/utils/prompts")


def get_prompt(prompt_name: str) -> str:
    try:
        with open(PROMPTS_DIR / f"{prompt_name}.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Prompt {prompt_name} not found in {PROMPTS_DIR}")


# NOTE: OAI logging requires system prompt to optimize


@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(5))
def openai_structured_output_request(
    prompt_name: str,
    model: str = "gpt-5",
    reasoning: str = "high",
    text_format: BaseModel = None,
    **format_kwargs,
) -> BaseModel:
    """Make a structured output request to OpenAI API.

    Args:
        prompt_name: Name of the prompt file (without .txt extension)
        model: OpenAI model to use
        reasoning: Reasoning effort level ("low", "medium", "high", "minimal")
        text_format: Pydantic BaseModel for structured output
        group_logging: If True, automatically sets MLflow experiment to prompt_name
        **format_kwargs: Variables to format into the prompt template
    """
    # Ensure we have a valid API key before making a request
    if client.api_key == "dummy_key_for_initialization":
        # Try to refresh key from environment in case it was set later
        real_key = os.environ.get("OPENAI_API_KEY")
        if real_key:
            client.api_key = real_key
        else:
            raise ValueError("OPENAI_API_KEY environment variable is not set.")

    prompt = get_prompt(prompt_name).format(**format_kwargs)
    response = client.responses.parse(
        model=model,
        reasoning={"effort": reasoning},
        input=[{"role": "user", "content": prompt}],
        text_format=text_format,
        metadata={"source": prompt_name},
    )
    return response.output_parsed


async def openai_structured_output_request_async(
    prompt_name: str,
    model: str = "gpt-5",
    reasoning: str = "high",
    text_format: BaseModel = None,
    metadata: dict[str, Any] = None,
    **format_kwargs,
) -> tuple[BaseModel, str]:
    """Make an async structured output request to OpenAI API.

    Args:
        prompt_name: Name of the prompt file (without .txt extension)
        model: OpenAI model to use
        reasoning: Reasoning effort level ("low", "medium", "high", "minimal")
        text_format: Pydantic BaseModel for structured output
        auto_set_experiment: If True, automatically sets MLflow experiment to prompt_name
        **format_kwargs: Variables to format into the prompt template
    """
    # Ensure we have a valid API key before making a request
    if async_client.api_key == "dummy_key_for_initialization":
        # Try to refresh key from environment in case it was set later
        real_key = os.environ.get("OPENAI_API_KEY")
        if real_key:
            async_client.api_key = real_key
        else:
            raise ValueError("OPENAI_API_KEY environment variable is not set.")

    prompt = get_prompt(prompt_name).format(**format_kwargs)

    async for attempt in AsyncRetrying(
        wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(5)
    ):
        with attempt:
            metadata = metadata or {}
            metadata["source"] = prompt_name
            response = await async_client.responses.parse(
                model=model,
                reasoning={"effort": reasoning},
                input=[{"role": "user", "content": prompt}],
                metadata=metadata,
                text_format=text_format,
            )
            return response.output_parsed, response.id


# SMARTER retry
# except RateLimitError as e:
#     # Extract retry_after from error message if available
#     retry_after = 1.0  # Default wait time in seconds
#     error_message = str(e)

#     # Try to extract retry time from error message
#     if "try again in" in error_message:
#         try:
#             # Extract time like "5.417s" or "24.709s"
#             match = re.search(r"try again in ([\d.]+)s", error_message)
#             if match:
#                 retry_after = float(match.group(1))
#                 # Add a small buffer
#                 retry_after = min(retry_after + 0.5, 30.0)  # Cap at 30 seconds
#         except (ValueError, AttributeError):
#             pass

#     if attempt < max_retries - 1:
#         # Exponential backoff, but cap at reasonable maximum
#         wait_time = min(retry_after * (2**attempt), 60.0)  # Cap at 60 seconds
#         logger.warning(
#             "Rate limit error (attempt %d/%d). Retrying after %.2f seconds...",
#             attempt + 1,
#             max_retries,
#             wait_time,
#         )
#         await asyncio.sleep(wait_time)
#     else:
#         logger.error(
#             "Rate limit error after %d attempts. Falling back to first candidate.",
#             max_retries,
#         )
#         # Fallback to first candidate when rate limits persist
#         return 0

# except Exception as e:
#     # For other errors, log and fall back
#     logger.error(
#         "Error calling OpenAI API: %s. Falling back to first candidate.", e
#     )
#     return 0
