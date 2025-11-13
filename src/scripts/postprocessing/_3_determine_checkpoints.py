import json
from concurrent.futures import ThreadPoolExecutor
from typing import List

from pydantic import BaseModel, Field

from config.storage import DATA_DIR
from utils.oai import openai_structured_output_request


class CheckpointExtractionResult(BaseModel):
    checkpoints_idx: List[int] = Field(
        description="The indexes of the checkpoints you extracted.",
        min_length=2,
        max_length=2,
    )
    checkpoints_reasoning: List[str] = Field(
        description="A 1 line reasoning on why you selected the checkpoints you did",
        min_length=2,
        max_length=2,
    )


def extract_checkpoints(
    task_description: str, steps_taken: List[dict], num_checkpoints: int = 2
):
    print(f"Extracting checkpoints for task: {task_description}")

    # Convert steps to JSON string for the prompt
    steps_str = json.dumps(steps_taken, indent=2)

    result = openai_structured_output_request(
        prompt_name="extract_checkpoints",
        model="gpt-5",
        reasoning="high",
        text_format=CheckpointExtractionResult,
        task_description=task_description,
        steps_taken=steps_str,
        num_checkpoints=num_checkpoints,
    )

    print(f"Extracted checkpoints: {result.checkpoints_idx}")
    return result


def main():
    with open(DATA_DIR / "tasks.jsonl", "r") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    with ThreadPoolExecutor(max_workers=32) as executor:
        futures = []
        for task in tasks:
            future = executor.submit(
                extract_checkpoints,
                task_description=task["task_description"],
                steps_taken=task["tool_calls"],
            )
            futures.append(future)

        for future, task in zip(futures, tasks):
            result = future.result()
            task["checkpoints"] = result.checkpoints_idx
            task["checkpoints_reasoning"] = result.checkpoints_reasoning

    with open(DATA_DIR / "tasks.jsonl", "w") as f:
        for task in tasks:
            f.write(json.dumps(task) + "\n")


if __name__ == "__main__":
    main()
