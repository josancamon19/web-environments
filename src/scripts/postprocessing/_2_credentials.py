from dataclasses import dataclass, field, asdict
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Dict

import dspy
import mlflow

from src.config.storage import DATA_DIR
from src.models import BaseToolCallData


@dataclass
class Credential:
    website: str = field(default="")
    fields: Dict[str, str] = field(default_factory=dict)
    tool_call_ids: List[int] = field(default_factory=list)


def get_credential_extractor():
    class CredentialExtractor(dspy.Signature):
        """
        You are in charge of reviewing a series of steps a human took to perform a task and identify any credentials
        (login information) that were entered during the task execution.

        Credentials can include:
        - Email addresses
        - Passwords
        - Usernames
        - Phone numbers

        Your goal is to extract these credentials and associate them with the correct website.

        Key things to consider:
        - Look for "type" actions where text is being entered into login/authentication forms
        - Identify the website from the URL in "go_to" actions or "navigates_to" fields
        - Extract the base domain (e.g., "amazon.com")
        - Determine what type of credential field it is based on the selector and context
        - Common field indicators: "email", "username", "password", "phone", "user", "pwd", "pass"
        - Note: Empty strings or placeholder text should NOT be considered credentials
        - Associate the credential with the tool call(s) that performed the action, by including those ids in the tool_call_ids field.
        """

        task_description: str = dspy.InputField(
            description="The description of the task"
        )
        trajectory: List[BaseToolCallData] = dspy.InputField(
            description="The steps taken by the human to perform the task."
        )
        credentials: List[Credential] = dspy.OutputField(
            description="List of credentials found in the trajectory.",
            default_factory=list,
        )

    return CredentialExtractor


def extract_credentials_from_trajectory(
    task_description: str, trajectory: List[BaseToolCallData]
) -> List[Credential]:
    print(f"Extracting credentials for task: {task_description[:60]}...")

    CredentialExtractor = get_credential_extractor()
    predictor = dspy.Predict(CredentialExtractor)

    trajectory = [
        BaseToolCallData(
            type=step.get("type"),
            params=step.get("params", {}),
            timestamp=step.get("timestamp"),
        )
        for step in trajectory
    ]

    result = predictor(task_description=task_description, trajectory=trajectory)

    if result.credentials:
        return result.credentials

    return []


def main():
    lm = dspy.LM(
        "openai/gpt-5",
        reasoning_effort="medium",
        temperature=1.0,
        max_tokens=16000,
    )
    mlflow.set_tracking_uri("http://127.0.0.1:5000")
    mlflow.set_experiment(
        f"extract-credentials-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
    )
    mlflow.dspy.autolog()
    dspy.configure(lm=lm)

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
            task["credentials"] = [asdict(credential) for credential in credentials]

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
