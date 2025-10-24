import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import dspy
import typer
import mlflow

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config.storage_config import DATA_DIR
from src.eval.judges import get_lm_judge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_doms_available_for_task(task_id: int, results_dir: Path) -> List[int]:
    """Get list of available DOM step indices for a task.

    Args:
        task_id: The task identifier
        results_dir: Path to the results directory

    Returns:
        Sorted list of step indices that have DOM files available
    """
    dom_dir = results_dir / "doms" / f"task_{task_id}"
    step_indices = []

    if not dom_dir.exists():
        logger.debug(f"DOM directory not found for task {task_id}: {dom_dir}")
        return step_indices

    for dom_file in dom_dir.glob("step_*.txt"):
        # Extract step number from filename like "step_5.txt"
        step_name = dom_file.stem  # Gets "step_5" from "step_5.txt"
        if step_name.startswith("step_"):
            try:
                step_idx = int(step_name[5:])  # Extract number after "step_"
                step_indices.append(step_idx)
            except ValueError:
                logger.warning(f"Invalid step filename: {dom_file}")
                continue

    return sorted(step_indices)


def evaluate_checkpoints_for_task(
    task_id: int,
    task_data: Dict[str, Any],
    model_data: Dict[str, Any],
    results_dir: Path,
) -> Dict[str, Any]:
    """Evaluate all checkpoints for a single failed task."""

    checkpoints = task_data.get("checkpoints", [])
    checkpoints_reasoning = task_data.get("checkpoints_reasoning", [])

    if len(checkpoints) != 2 or len(checkpoints_reasoning) != 2:
        logger.warning(f"Task {task_id} does not have exactly 2 checkpoints, skipping")
        return None

    # Extract task description and trajectories from task_data and model_data
    task_description = task_data.get("task_description", "")
    human_trajectory = task_data.get("tool_calls", [])
    model_trajectory = model_data.get("tool_calls", [])

    def evaluate_single_checkpoint(
        checkpoint_idx: int, checkpoint_reasoning: str
    ) -> Dict[str, Any]:
        judge = get_lm_judge(results_dir)

        result = judge(
            task_id=task_id,
            task_description=task_description,
            human_trajectory=human_trajectory,
            agent_trajectory=model_trajectory,
            checkpoint_index=checkpoint_idx,
            checkpoint_reasoning=checkpoint_reasoning,
            agent_doms_available=_get_doms_available_for_task(task_id, results_dir),
        )

        return {
            "achieved": result.achieved,
            "reasoning": result.reasoning,
            "confidence": result.confidence,
            "score": 0.0 if not result.achieved else 0.33,
        }

    checkpoint_results = {}
    total_score = 0.0
    for idx in range(2):
        logger.info(f"Evaluating task {task_id}, checkpoint {idx}...")

        try:
            result = evaluate_single_checkpoint(
                checkpoint_idx=checkpoints[idx],
                checkpoint_reasoning=checkpoints_reasoning[idx],
            )

            checkpoint_results[f"checkpoint_{idx}"] = result
            total_score += result["score"]

            logger.info(
                f"Task {task_id}, checkpoint {idx}: "
                f"{'achieved' if result['achieved'] else 'not achieved'} "
                f"(confidence: {result['confidence']:.2f})"
            )
            if not result["score"]:
                break
        except Exception as e:
            logger.error(f"Failed to evaluate checkpoint {idx} for task {task_id}: {e}")
            checkpoint_results[f"checkpoint_{idx}"] = {
                "achieved": False,
                "reasoning": f"Evaluation error: {str(e)}",
                "confidence": 0.0,
                "score": 0.0,
            }

    checkpoint_results["total_checkpoint_score"] = round(total_score, 2)

    return checkpoint_results


def evaluate_checkpoints(results_dir: str, judge_model: str):
    """Evaluate checkpoints for all failed tasks and update grade.json."""

    lm = dspy.LM(
        f"openai/{judge_model}",
        reasoning_effort="high",
        temperature=1.0,
        max_tokens=120000,
    )
    mlflow.set_tracking_uri("http://127.0.0.1:5000")
    mlflow.set_experiment(f"eval-checkpoints-{results_dir.split('/')[-1]}")
    mlflow.dspy.autolog()
    dspy.configure(lm=lm)

    results_dir = Path(results_dir)
    grade_json_path = results_dir / "grade.json"

    if not grade_json_path.exists():
        logger.error(f"grade.json not found at: {grade_json_path}")
        logger.error("Please run evaluate.py first to generate grade.json")
        return None

    # Load existing grade.json
    with open(grade_json_path, "r") as f:
        grade_data = json.load(f)

    # Load tasks.jsonl for checkpoint data
    human_tasks_by_id = {}
    with open(DATA_DIR / "tasks.jsonl", "r") as f:
        for line in f:
            if not line.strip():
                continue
            task = json.loads(line)
            human_tasks_by_id[task["task_id"]] = task

    # Load model results
    results_json_dir = results_dir / "results"
    model_tasks_by_id = {}
    for json_file in sorted(results_json_dir.glob("*.json")):
        try:
            with open(json_file, "r") as f:
                model_task = json.load(f)
                model_tasks_by_id[model_task["task_id"]] = model_task
        except Exception as e:
            logger.warning(f"Failed to read {json_file}: {e}")
            continue

    # Find failed tasks (correct=False)
    failed_task_ids = [
        task_id
        for task_id, result in grade_data["task_results"].items()
        if not result["correct"]
    ][0:1]

    logger.info(f"Found {len(failed_task_ids)} failed tasks to evaluate checkpoints")

    def evaluate_task_wrapper(task_id_str):
        """Wrapper for parallel execution."""
        task_id = int(task_id_str)
        print("task_id", task_id)

        if task_id not in human_tasks_by_id:
            logger.warning(f"Task {task_id} not found in tasks.jsonl")
            return task_id_str, None

        if task_id not in model_tasks_by_id:
            logger.warning(f"Task {task_id} not found in model results")
            return task_id_str, None

        human_task = human_tasks_by_id[task_id]
        model_task = model_tasks_by_id[task_id]

        checkpoint_results = evaluate_checkpoints_for_task(
            task_id=task_id,
            task_data=human_task,
            model_data=model_task,
            results_dir=results_dir,
        )

        return task_id_str, checkpoint_results

    # Run checkpoint evaluations in parallel
    checkpoint_evaluations = {}

    with ThreadPoolExecutor(max_workers=16) as executor:
        future_to_task = {
            executor.submit(evaluate_task_wrapper, task_id): task_id
            for task_id in failed_task_ids
        }

        for future in as_completed(future_to_task):
            task_id, result = future.result()
            if result is not None:
                checkpoint_evaluations[task_id] = result

    # Update grade.json with checkpoint results
    for task_id, checkpoint_result in checkpoint_evaluations.items():
        if task_id in grade_data["task_results"]:
            grade_data["task_results"][task_id]["checkpoint_evaluation"] = (
                checkpoint_result
            )

    # Compute checkpoint statistics
    checkpoint_0_achieved = sum(
        1
        for r in checkpoint_evaluations.values()
        if r.get("checkpoint_0", {}).get("achieved", False)
    )
    checkpoint_1_achieved = sum(
        1
        for r in checkpoint_evaluations.values()
        if r.get("checkpoint_1", {}).get("achieved", False)
    )

    total_evaluated = len(checkpoint_evaluations)
    total_checkpoint_scores = [
        r["total_checkpoint_score"] for r in checkpoint_evaluations.values()
    ]

    avg_checkpoint_score = (
        sum(total_checkpoint_scores) / len(total_checkpoint_scores)
        if total_checkpoint_scores
        else 0
    )

    tasks_with_partial_credit = sum(1 for score in total_checkpoint_scores if score > 0)

    checkpoint_stats = {
        "failed_tasks_evaluated": total_evaluated,
        "avg_checkpoint_score": round(avg_checkpoint_score, 3),
        "checkpoint_0_success_rate": round(checkpoint_0_achieved / total_evaluated, 3)
        if total_evaluated > 0
        else 0,
        "checkpoint_1_success_rate": round(checkpoint_1_achieved / total_evaluated, 3)
        if total_evaluated > 0
        else 0,
        "tasks_with_partial_credit": tasks_with_partial_credit,
    }

    # Add checkpoint stats to grade data
    grade_data["checkpoint_stats"] = checkpoint_stats

    # Save updated grade.json
    with open(grade_json_path, "w") as f:
        json.dump(grade_data, f, indent=2)

    logger.info(f"Updated grade.json with checkpoint evaluations at {grade_json_path}")

    # Print summary
    print("\n" + "=" * 50)
    print("CHECKPOINT EVALUATION SUMMARY")
    print("=" * 50)
    print(f"Failed tasks evaluated: {total_evaluated}")
    print(f"Average checkpoint score: {avg_checkpoint_score:.3f} / 0.66")
    print(
        f"Checkpoint 0 success rate: {checkpoint_stats['checkpoint_0_success_rate']:.1%}"
    )
    print(
        f"Checkpoint 1 success rate: {checkpoint_stats['checkpoint_1_success_rate']:.1%}"
    )
    print(f"Tasks with partial credit: {tasks_with_partial_credit}")
    print("=" * 50)

    return checkpoint_evaluations


app = typer.Typer()


@app.command()
def main(
    results_dir: str,
    judge_model: str = typer.Option("gpt-5", help="Judge model for evaluation"),
):
    """Evaluate checkpoints for failed tasks to provide partial credit."""
    print(f"Evaluating checkpoints for: {results_dir}")
    print(f"Using judge model: {judge_model}")
    print("-" * 50)

    # Run checkpoint evaluation
    evaluations = evaluate_checkpoints(results_dir, judge_model)

    if evaluations:
        print("\nCheckpoint evaluation completed successfully!")
        print(
            f"Updated grade.json with checkpoint results for {len(evaluations)} tasks"
        )
    else:
        print("Checkpoint evaluation failed or no tasks to evaluate")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
