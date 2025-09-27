#!/usr/bin/env python3
"""
Standalone script to evaluate model outputs using the LLM judge.
Usage: python evaluate_outputs.py <model_name>
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, List
from litellm import completion

import sys
import os
import dspy
from src.tasks.db_to_jsonl_format import BaseToolCallData

if "--prod" in sys.argv:
    DATA_DIR = os.path.join("data", "prod")
else:
    DATA_DIR = os.path.join("data", "dev")

# Add src to path
sys.path.insert(0, ".")

lm = dspy.LM(
    "openai/o3-2025-04-16",
    reasoning_effort="high",
    temperature=1.0,
    max_tokens=64000,
)
dspy.configure(lm=lm)


# TODO: should be an agent.
class JudgeCompletion(dspy.Signature):
    # - clarify that in the way the human trajectory and dom match the description, should determine if model last_step matches the dom and task completion, model not hallucinating.
    """
    You are a judge for evaluating the performance of a model in completing a browser task.
    You will be given the golden trajectory, meaning the human trajectory that executed the task, including the last dom the human saw, and the answer the human provided.
    - consider the human answer is potentially very brief but matches the task description and it is grounded on the DOM.

    You will also be given
    1. the model's completion step data, which contains the model's state, tabs opened, url's, page title, thinking, and answer.
    2. the model's trajectory, which contains the model's actions.
    3. the model's last dom accessed.

    Your task is to ensure the model's answer is correct given the task description, grounded on the DOM, and also mimicks human actions and response.
    The answers might be different from human to model, because
    1) the web is in constant change, products, lists, stocks constantly change.
    2) the website might've been updated, thus some steps/navigations might differ as well.

    """

    task: str = dspy.InputField(description="The task description")
    # ====
    agent_completion: Dict[str, Any] = dspy.InputField(
        description="The last model step dump contents"
    )
    agent_trajectory: List[BaseToolCallData] = dspy.InputField(
        description="The model's trajectory"
    )
    agent_dom: str = dspy.InputField(description="The last model step dom contents")
    # ====
    human_trajectory: List[BaseToolCallData] = dspy.InputField(
        description="The human trajectory"
    )
    human_dom: str = dspy.InputField(description="The last human dom contents")
    human_answer: str = dspy.InputField(description="The human answer")
    # ====

    correct: bool = dspy.OutputField(
        description="Whether the model's answer is correct or not"
    )
    reasoning: str = dspy.OutputField(description="The reasoning for your judgement")
    confidence: float = dspy.OutputField(
        description="The confidence score for your judgement"
    )


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def evaluate_model_outputs(model: str, judge_model: str = "gpt-4.1-2025-04-14"):
    output_file = (
        Path("src/eval/results") / f"browseruse-{model.replace('/', '-')}.jsonl"
    )
    print(output_file)
    if not output_file.exists():
        logger.error(f"Output file not found: {output_file}")
        return None

    # Load the original tasks to get correct answers
    tasks_by_id = {}
    with open(Path(f"{DATA_DIR}/tasks.jsonl"), "r") as f:
        for line in f:
            if line.strip():
                task = json.loads(line)
                tasks_by_id[task["task_id"]] = task

    # Evaluate each result
    evaluation_data = []
    with open(output_file, "r") as f:
        for line in f:
            if line.strip():
                model_task = json.loads(line)
                task_id = model_task["task_id"]

                # Skip if not an information retrieval task
                if model_task.get("task_type") != "information_retrieval":
                    logger.info(f"Skipping task {task_id} (not information retrieval)")
                    continue

                # Get the correct answer from original task
                if task_id not in tasks_by_id:
                    logger.warning(f"Task {task_id} not found in original tasks")
                    continue

                model_completion_step = None
                for step in model_task["dump"]:
                    done = step["model_output"].get("action", [{}])[-1].get("done")
                    if done:
                        del step["result"]
                        model_completion_step = step

                if not model_completion_step:
                    logger.warning(f"No final step found for task {task_id}")
                    # TODO: raise is bad already.
                    continue

                model_trajectory = model_task["tool_calls"]
                model_last_dom = model_task["step_dom_mapping"][
                    str(len(model_trajectory))
                ]
                model_last_dom_contents = open(
                    Path(DATA_DIR) / model_last_dom, "r"
                ).read()
                human_task = tasks_by_id[task_id]

                print(json.dumps(model_trajectory, indent=2))


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "o3-2025-04-16"
    judge_model = sys.argv[2] if len(sys.argv) > 2 else "gpt-4.1-2025-04-14"

    print(f"Evaluating outputs for model: {model}")
    print(f"Using judge model: {judge_model}")
    print("-" * 50)

    # Run evaluation
    evaluation = evaluate_model_outputs(model, judge_model)

    if evaluation:
        print("\n" + "=" * 50)
        print("EVALUATION SUMMARY")
        print("=" * 50)
        print(f"Model: {model}")
        print(f"Judge: {judge_model}")
        print(f"Tasks evaluated: {evaluation['total_count']}")
        print(f"Correct: {evaluation['correct_count']}")
        print(f"Accuracy: {evaluation['accuracy']:.2f}%")

        # Show task-level results
        print("\nTask-level results:")
        for result in evaluation["results"]:
            status = "✓" if result["correct"] else "✗"
            print(
                f"  {status} Task {result['task_id']}: {result['task_description'][:50]}..."
            )
            if not result["correct"]:
                print(f"    Reason: {result['reasoning'][:100]}...")
    else:
        print("Evaluation failed or no results found")
        sys.exit(1)


if __name__ == "__main__":
    main()
