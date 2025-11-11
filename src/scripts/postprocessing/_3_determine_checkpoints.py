import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List

import dspy
import mlflow

from src.config.storage import DATA_DIR
from src.models import BaseToolCallData


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
    CheckpointExtractor = get_checkpoint_extractor()
    predictor = dspy.Predict(CheckpointExtractor)
    result = predictor(task_description=task_description, steps_taken=steps_taken)
    print(f"Extracted checkpoints: {result.checkpoints_idx}")
    return result


def main():
    lm = dspy.LM(
        "openai/gpt-5",
        reasoning_effort="high",
        temperature=1.0,
        max_tokens=24000,
    )
    mlflow.set_tracking_uri("http://127.0.0.1:5000")
    mlflow.set_experiment(
        f"extract-checkpoints-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
    )
    mlflow.dspy.autolog()
    dspy.configure(lm=lm)

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
