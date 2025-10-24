import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import dspy
import mlflow
import typer

from src.config.storage_config import DATA_DIR
from src.eval.judges import JudgeCompletion

# Disable MLflow logging to avoid spam warnings
os.environ["MLFLOW_TRACKING_URI"] = ""
mlflow.set_tracking_uri("")
logging.getLogger("mlflow").setLevel(logging.ERROR)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def evaluate_model_outputs(results_dir: str, judge_model: str):
    lm = dspy.LM(
        f"openai/{judge_model}",
        reasoning_effort="high",
        temperature=1.0,
        max_tokens=64000,
    )
    mlflow.set_tracking_uri("http://127.0.0.1:5000")
    mlflow.set_experiment(f"eval-{results_dir.split('/')[-1]}")
    mlflow.dspy.autolog()
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

    def _get_model_completion_step(
        model_task: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Find the completion step where the model signaled done."""
        for step in model_task.get("dump", []):
            # Check if this step has a "done" action
            actions = step.get("model_output", {}).get("action", [])
            if actions and isinstance(actions, list) and len(actions) > 0:
                last_action = actions[-1]
                if isinstance(last_action, dict) and "done" in last_action:
                    # Found completion step - remove result to reduce size
                    step_copy = step.copy()
                    if "result" in step_copy:
                        del step_copy["result"]
                    return step_copy

            # Also check result for is_done flag
            results = step.get("result", [])
            if results and isinstance(results, list):
                for result in results:
                    if isinstance(result, dict) and result.get("is_done") is True:
                        step_copy = step.copy()
                        if "result" in step_copy:
                            del step_copy["result"]
                        return step_copy

        return None

    def evaluate_single_task(model_task):
        """Evaluate a single task - used for parallel processing"""
        task_id = model_task["task_id"]

        if model_task.get("task_type") != "information_retrieval":
            logger.info(f"Skipping task {task_id} (not information retrieval)")
            return task_id, None

        model_completion_step = _get_model_completion_step(model_task)
        if not model_completion_step:
            logger.warning(
                f"No completion step found for task {task_id} - model did not call 'done'"
            )
            return task_id, {
                "correct": False,
                "reasoning": "Model did not complete the task - no 'done' action was called (likely hit step limit or encountered error)",
                "confidence": 0,
            }

        model_trajectory = model_task.get("tool_calls", [])

        # Find the last available DOM step (may not match trajectory length)
        step_dom_mapping = model_task.get("step_dom_mapping", {})
        if not step_dom_mapping:
            logger.warning(f"No DOM mapping found for task {task_id}")
            return task_id, {
                "correct": False,
                "reasoning": "No DOM states captured in model trajectory",
                "confidence": 0,
            }

        last_step = max(int(k) for k in step_dom_mapping.keys())
        model_last_dom_path = step_dom_mapping[str(last_step)]

        try:
            model_last_dom = open(results_dir / model_last_dom_path, "r").read()
        except Exception as e:
            logger.warning(f"Failed to read model DOM for task {task_id}: {e}")
            return task_id, {
                "correct": False,
                "reasoning": f"Error reading model DOM file: {e}",
                "confidence": 0,
            }
        # ===
        human_task = human_tasks_by_id[task_id]
        human_trajectory = human_task["tool_calls"]

        # Find the last tool call with a dom_state
        human_last_dom_path = None
        for tool_call in reversed(human_trajectory):
            if "dom_state" in tool_call.get("params", {}):
                human_last_dom_path = tool_call["params"]["dom_state"]
                break

        # If no DOM state in human trajectory, use a placeholder
        # (some simple tasks may only have go_to with no DOM capture)
        if not human_last_dom_path:
            logger.warning(
                f"No dom_state found in human trajectory for task {task_id} - using placeholder"
            )
            human_last_dom = "[Human trajectory has no DOM capture - likely a simple task with only initial page load]"
        else:
            try:
                human_last_dom = open(DATA_DIR / human_last_dom_path, "r").read()
            except Exception as e:
                logger.warning(f"Failed to read human DOM for task {task_id}: {e}")
                human_last_dom = f"[Error reading human DOM: {e}]"

        human_answer = human_task["answer"]
        # breakpoint()
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

    # Compute comprehensive statistics
    correct_results = [r for r in evaluations.values() if r["correct"]]
    incorrect_results = [r for r in evaluations.values() if not r["correct"]]

    total_tasks = len(evaluations)
    correct_count = len(correct_results)
    incorrect_count = len(incorrect_results)

    # Categorize failure reasons
    failure_categories = {
        "model_incomplete": 0,  # Model didn't call done
        "dom_issues": 0,  # DOM reading or mapping issues
        "incorrect_answer": 0,  # Model completed but answer was wrong
    }

    for result in incorrect_results:
        reasoning = result.get("reasoning", "").lower()
        if "did not complete" in reasoning or "no 'done' action" in reasoning:
            failure_categories["model_incomplete"] += 1
        elif "dom" in reasoning or "error reading" in reasoning:
            failure_categories["dom_issues"] += 1
        else:
            failure_categories["incorrect_answer"] += 1

    # Confidence statistics
    all_confidences = [r["confidence"] for r in evaluations.values()]
    avg_confidence = (
        sum(all_confidences) / len(all_confidences) if all_confidences else 0
    )

    correct_confidences = [r["confidence"] for r in correct_results]
    avg_confidence_correct = (
        sum(correct_confidences) / len(correct_confidences)
        if correct_confidences
        else 0
    )

    incorrect_confidences = [r["confidence"] for r in incorrect_results]
    avg_confidence_incorrect = (
        sum(incorrect_confidences) / len(incorrect_confidences)
        if incorrect_confidences
        else 0
    )

    # Confidence distribution (bins: 0-0.3, 0.3-0.5, 0.5-0.7, 0.7-0.9, 0.9-1.0)
    confidence_bins = {
        "0.0-0.3": 0,
        "0.3-0.5": 0,
        "0.5-0.7": 0,
        "0.7-0.9": 0,
        "0.9-1.0": 0,
    }

    for conf in all_confidences:
        if conf < 0.3:
            confidence_bins["0.0-0.3"] += 1
        elif conf < 0.5:
            confidence_bins["0.3-0.5"] += 1
        elif conf < 0.7:
            confidence_bins["0.5-0.7"] += 1
        elif conf < 0.9:
            confidence_bins["0.7-0.9"] += 1
        else:
            confidence_bins["0.9-1.0"] += 1

    # Min/max confidence
    min_confidence = min(all_confidences) if all_confidences else 0
    max_confidence = max(all_confidences) if all_confidences else 0

    # Compile results with statistics
    results_with_stats = {
        "metadata": {
            "judge_model": judge_model,
            "results_dir": str(results_dir),
            "total_model_tasks": len(model_tasks),
            "evaluated_tasks": total_tasks,
        },
        "summary": {
            "total_evaluated": total_tasks,
            "correct": correct_count,
            "incorrect": incorrect_count,
            "accuracy": round(correct_count / total_tasks * 100, 2)
            if total_tasks > 0
            else 0,
        },
        "failure_analysis": {
            "model_incomplete": failure_categories["model_incomplete"],
            "dom_issues": failure_categories["dom_issues"],
            "incorrect_answer": failure_categories["incorrect_answer"],
            "model_incomplete_pct": round(
                failure_categories["model_incomplete"] / total_tasks * 100, 2
            )
            if total_tasks > 0
            else 0,
            "dom_issues_pct": round(
                failure_categories["dom_issues"] / total_tasks * 100, 2
            )
            if total_tasks > 0
            else 0,
            "incorrect_answer_pct": round(
                failure_categories["incorrect_answer"] / total_tasks * 100, 2
            )
            if total_tasks > 0
            else 0,
        },
        "confidence_stats": {
            "overall": {
                "mean": round(avg_confidence, 3),
                "min": round(min_confidence, 3),
                "max": round(max_confidence, 3),
            },
            "correct_answers": {
                "mean": round(avg_confidence_correct, 3),
                "count": correct_count,
            },
            "incorrect_answers": {
                "mean": round(avg_confidence_incorrect, 3),
                "count": incorrect_count,
            },
            "distribution": confidence_bins,
        },
        "task_results": evaluations,
    }

    # Save evaluations to file
    output_path = results_dir / "grade.json"
    with open(output_path, "w") as f:
        json.dump(results_with_stats, f, indent=2)
    logger.info(f"Saved evaluations to {output_path}")

    print(json.dumps(results_with_stats, indent=2))
    return evaluations


app = typer.Typer()


@app.command()
def main(
    results_dir: str,
    judge_model: str = typer.Option("gpt-5", help="Judge model for evaluation"),
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
        print(
            f"Average Confidence: {sum(result['confidence'] for result in evaluation.values()) / len(evaluation):.3f}"
        )

        # Show task-level results
        print("\nTask-level results:")
        for result in evaluation.values():
            status = "✓" if result["correct"] else "✗"
            print(
                f"   Status: {status} Conf: {result['confidence']:.2f} Reason: {result['reasoning'][:80]}..."
            )
    else:
        print("Evaluation failed or no results found")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
