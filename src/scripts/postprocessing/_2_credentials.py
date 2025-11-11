import json
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

from pydantic import BaseModel, Field

from src.config.storage import DATA_DIR
from utils.oai import openai_structured_output_request


class Credential(BaseModel):
    website: str = Field(
        default="", description="The website domain (e.g., amazon.com)"
    )
    fields: Dict[str, str] = Field(
        default_factory=dict,
        description="Dictionary of credential field names to values",
    )
    tool_call_ids: List[int] = Field(
        default_factory=list,
        description="IDs of tool calls that entered these credentials",
    )


class CredentialExtractionResult(BaseModel):
    credentials: List[Credential] = Field(
        default_factory=list, description="List of credentials found in the trajectory."
    )


def extract_credentials_from_trajectory(
    task_description: str, trajectory: List[dict]
) -> List[Credential]:
    print(f"Extracting credentials for task: {task_description[:60]}...")

    # Convert trajectory to JSON string for the prompt
    trajectory_str = json.dumps(trajectory, indent=2)

    result = openai_structured_output_request(
        prompt_name="extract_credentials",
        model="gpt-5",
        reasoning="medium",
        group_logging=True,
        text_format=CredentialExtractionResult,
        task_description=task_description,
        trajectory=trajectory_str,
    )

    if result.credentials:
        return result.credentials

    return []


def main():
    with open(DATA_DIR / "tasks.jsonl", "r") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    print(f"Processing {len(tasks)} tasks for credential extraction...\n")

    with ThreadPoolExecutor(max_workers=32) as executor:
        futures = []
        for task in tasks:
            future = executor.submit(
                extract_credentials_from_trajectory,
                task_description=task["task_description"],
                trajectory=task["tool_calls"],
            )
            futures.append(future)

        for future, task in zip(futures, tasks):
            credentials = future.result()
            task["credentials"] = [
                credential.model_dump() for credential in credentials
            ]

    with open(DATA_DIR / "tasks.jsonl", "w") as f:
        for task in tasks:
            f.write(json.dumps(task) + "\n")

    tasks_with_credentials = sum(1 for t in tasks if t.get("credentials", []))
    print("\n" + "=" * 60)
    print(f"Summary: Found credentials in {tasks_with_credentials}/{len(tasks)} tasks")
    print("Updated tasks.jsonl with credential information")
    print("=" * 60)


if __name__ == "__main__":
    main()
