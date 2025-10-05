import json
from concurrent.futures import ThreadPoolExecutor
from typing import List
from pathlib import Path
import dspy
from tasks.db_to_jsonl_format import BaseToolCallData

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config.storage_config import DATA_DIR


lm = dspy.LM(
    "openai/gpt-5",
    reasoning_effort="high",
    temperature=1.0,
    max_tokens=24000,
)
dspy.configure(lm=lm)


def get_checkpoint_extractor(checkpoints: int = 2):
    class CheckpointExtractor(dspy.Signature):
        """
        You are in charge of reviewing a series of steps a human took to perform a task and identify the steps in the series that are most likely to be the most important for the human to have performed to complete the task.
        This steps are checkpoints that we could assume the human achieved part of the task.

        The checkpoints you extract will be used to evaluate the performance of other humans and language models, make sure you consider this when selecting the checkpoints.

        Key things to consider to detemrine importance of a step:
        - Your rationale of the whole set of steps and the key actions or events that have more likely determine the completion of the task.
        - The tool call action that was performed
        - Consider "navigates_to" field and weight it's relevance
        - Consider the "timestamp" difference between tool_calls, might indicate longer wait times, more complex reasoning from the human, etc. But could also indicate a longer loading time, or network latency, so evaluate accordingly.

        Note: The step 0 or tool_call with type go_to should not be considered a checkpoint, cause that's the minimum expected, to open a browser page.
        """

        task_description: str = dspy.InputField(
            description="The description of the task"
        )
        steps_taken: List[BaseToolCallData] = dspy.InputField(
            description="The steps taken by the human to perform the task."
        )
        # dom_states_files: List[Dict[str, Any]] = dspy.InputField(
        #     description="Some of the dom_states files "
        # )
        checkpoints_idx: List[int] = dspy.OutputField(
            description="The indexes of the checkpoints you extracted.",
            min_length=checkpoints,
            max_length=checkpoints,
        )
        checkpoints_reasoning: List[str] = dspy.OutputField(
            description="A 1 line reasoning on why you selected the checkpoints you did",
            min_length=checkpoints,
            max_length=checkpoints,
        )

    return CheckpointExtractor


def extract_checkpoints(task_description: str, steps_taken: List[BaseToolCallData]):
    print(f"Extracting checkpoints for task: {task_description}")

    # Create a dspy predictor with the signature
    CheckpointExtractor = get_checkpoint_extractor()
    predictor = dspy.Predict(CheckpointExtractor)

    # Execute the predictor
    result = predictor(task_description=task_description, steps_taken=steps_taken)
    print(f"Extracted checkpoints: {result.checkpoints_idx}")
    return result


def update_parsed_tasks_with_checkpoints():
    with open(DATA_DIR / "tasks.jsonl", "r") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    # Use ThreadPoolExecutor to handle concurrent execution with proper result handling
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Submit all tasks to the executor
        futures = []
        for task in tasks:
            future = executor.submit(
                extract_checkpoints,
                task_description=task["task_description"],
                steps_taken=task["tool_calls"],
            )
            futures.append(future)

        # Collect results as they complete
        for future, task in zip(futures, tasks):
            result = future.result()
            task["checkpoints"] = result.checkpoints_idx
            task["checkpoints_reasoning"] = result.checkpoints_reasoning

    with open(DATA_DIR / "tasks.jsonl", "w") as f:
        for task in tasks:
            f.write(json.dumps(task) + "\n")


if __name__ == "__main__":
    update_parsed_tasks_with_checkpoints()
