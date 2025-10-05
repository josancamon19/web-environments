import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import dspy
import typer
from tasks.db_to_jsonl_format import BaseToolCallData

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config.storage_config import DATA_DIR


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


def evaluate_model_outputs(results_dir: str, judge_model: str):
    lm = dspy.LM(
        f"openai/{judge_model}",
        reasoning_effort="high",
        temperature=1.0,
        max_tokens=64000,
    )
    dspy.configure(lm=lm)

    results_dir = Path(results_dir)
    results_json_dir = results_dir / "results"
    print(f"Reading results from: {results_json_dir}")

    if not results_json_dir.exists():
        logger.error(f"Results directory not found: {results_json_dir}")
        return None

    # Load the original tasks to get correct answers
    human_tasks_by_id = {}
    with open(DATA_DIR / "tasks.jsonl", "r") as f:
        for line in f:
            if not line.strip():
                continue
            task = json.loads(line)
            human_tasks_by_id[task["task_id"]] = task

    # Evaluate each result - read individual JSON files
    model_tasks = []
    for json_file in sorted(results_json_dir.glob("*.json")):
        try:
            with open(json_file, "r") as f:
                model_task = json.load(f)
                model_tasks.append(model_task)
        except Exception as e:
            logger.warning(f"Failed to read {json_file}: {e}")
            continue

    def _get_model_completion_step(model_task: Dict[str, Any]) -> Dict[str, Any]:
        for step in model_task["dump"]:
            done = step["model_output"].get("action", [{}])[-1].get("done")
            if done:
                del step["result"]
                return step

    def evaluate_single_task(model_task):
        """Evaluate a single task - used for parallel processing"""
        task_id = model_task["task_id"]

        if model_task.get("task_type") != "information_retrieval":
            logger.info(f"Skipping task {task_id} (not information retrieval)")
            return task_id, None

        model_completion_step = _get_model_completion_step(model_task)
        if not model_completion_step:
            logger.warning(f"No final step found for task {task_id}")
            return task_id, {
                "correct": False,
                "reasoning": "No final step found",
                "confidence": 0,
            }

        model_trajectory = model_task["tool_calls"]
        model_last_dom = model_task["step_dom_mapping"][str(len(model_trajectory))]
        model_last_dom = open(results_dir / model_last_dom, "r").read()
        # ===
        human_task = human_tasks_by_id[task_id]
        human_trajectory = human_task["tool_calls"]

        # Find the last tool call with a dom_state
        human_last_dom_path = None
        for tool_call in reversed(human_trajectory):
            if "dom_state" in tool_call.get("params", {}):
                human_last_dom_path = tool_call["params"]["dom_state"]
                break

        if not human_last_dom_path:
            logger.warning(f"No dom_state found in human trajectory for task {task_id}")
            return task_id, {
                "correct": False,
                "reasoning": "No dom_state found in human trajectory",
                "confidence": 0,
            }

        human_last_dom = open(DATA_DIR / human_last_dom_path, "r").read()
        human_answer = human_task["answer"]
        # ===
        logger.info(f"Evaluating task {task_id}...")
        judge = dspy.Predict(JudgeCompletion)
        result = judge(
            task=human_task["task_description"],
            agent_completion=model_completion_step,
            agent_trajectory=model_trajectory,
            agent_dom=model_last_dom,
            human_trajectory=human_trajectory,
            human_dom=human_last_dom,
            human_answer=human_answer,
        )
        logger.info(f"Task {task_id} evaluated: {result.correct}")
        return task_id, {
            "correct": result.correct,
            "reasoning": result.reasoning,
            "confidence": result.confidence,
        }

    # Run evaluations in parallel
    evaluations = {}

    with ThreadPoolExecutor(max_workers=32) as executor:
        # Submit all tasks
        future_to_task = {
            executor.submit(evaluate_single_task, task): task for task in model_tasks
        }

        # Process results as they complete
        for future in as_completed(future_to_task):
            task_id, result = future.result()
            if result is not None:
                evaluations[task_id] = result

    # Save evaluations to file
    output_path = results_dir / "grade.json"
    with open(output_path, "w") as f:
        json.dump(evaluations, f, indent=2)
    logger.info(f"Saved evaluations to {output_path}")

    print(json.dumps(evaluations, indent=2))
    return evaluations


app = typer.Typer()


@app.command()
def main(
    judge_model: str = typer.Option("gpt-5", help="Judge model for evaluation"),
    results_dir: Optional[str] = typer.Option(
        None, help="Specific results directory path"
    ),
):
    """Evaluate model outputs on browser tasks."""
    print(f"Evaluating outputs for model: {results_dir}")
    print(f"Using judge model: {judge_model}")
    if results_dir:
        print(f"Results directory: {results_dir}")
    print("-" * 50)

    # Run evaluation
    evaluation = evaluate_model_outputs(results_dir, judge_model)

    if evaluation:
        print("\n" + "=" * 50)
        print("EVALUATION SUMMARY")
        print("=" * 50)
        print(f"Judge: {judge_model}")
        print(f"Tasks evaluated: {len(evaluation)}")
        print(f"Correct: {sum(result['correct'] for result in evaluation.values())}")
        print(
            f"Accuracy: {sum(result['correct'] for result in evaluation.values()) / len(evaluation) * 100:.2f}%"
        )

        # Show task-level results
        print("\nTask-level results:")
        for result in evaluation.values():
            status = "✓" if result["correct"] else "✗"
            print(f"   Status: {status} Reason: {result['reasoning'][:100]}...")
    else:
        print("Evaluation failed or no results found")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
