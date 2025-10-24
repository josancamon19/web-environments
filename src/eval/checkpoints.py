import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

import dspy
import mlflow
import typer

from src.config.storage_config import DATA_DIR
from src.eval.judges import get_lm_judge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
MAX_PARALLEL_WORKERS = 16
CHECKPOINT_SCORE = 0.33
NUM_CHECKPOINTS = 2


def _get_doms_available_for_task(task_id: int, results_dir: Path) -> List[int]:
    """Get list of available DOM step indices for a task."""
    dom_dir = results_dir / "doms" / f"task_{task_id}"

    if not dom_dir.exists():
        logger.debug(f"DOM directory not found for task {task_id}: {dom_dir}")
        return []

    step_indices = []
    for dom_file in dom_dir.glob("step_*.txt"):
        try:
            # Extract step number from filename like "step_5.txt"
            step_idx = int(dom_file.stem[5:])
            step_indices.append(step_idx)
        except (ValueError, IndexError):
            logger.warning(f"Invalid step filename: {dom_file}")

    return sorted(step_indices)


def _evaluate_single_checkpoint(
    task_id: int,
    checkpoint_idx: int,
    checkpoint_reasoning: str,
    task_description: str,
    human_trajectory: List,
    model_trajectory: List,
    results_dir: Path,
) -> Dict[str, Any]:
    """Evaluate a single checkpoint."""
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
        "score": CHECKPOINT_SCORE if result.achieved else 0.0,
    }


def evaluate_checkpoints_for_task(
    task_id: int,
    task_data: Dict[str, Any],
    model_data: Dict[str, Any],
    results_dir: Path,
) -> Dict[str, Any]:
    """Evaluate all checkpoints for a single failed task."""
    checkpoints = task_data.get("checkpoints", [])
    checkpoints_reasoning = task_data.get("checkpoints_reasoning", [])

    if (
        len(checkpoints) != NUM_CHECKPOINTS
        or len(checkpoints_reasoning) != NUM_CHECKPOINTS
    ):
        logger.warning(
            f"Task {task_id} does not have exactly {NUM_CHECKPOINTS} checkpoints, skipping"
        )
        return None

    task_description = task_data.get("task_description", "")
    human_trajectory = task_data.get("tool_calls", [])
    model_trajectory = model_data.get("tool_calls", [])

    checkpoint_results = {}
    total_score = 0.0

    for idx in range(NUM_CHECKPOINTS):
        logger.info(f"Evaluating task {task_id}, checkpoint {idx}...")

        try:
            result = _evaluate_single_checkpoint(
                task_id=task_id,
                checkpoint_idx=checkpoints[idx],
                checkpoint_reasoning=checkpoints_reasoning[idx],
                task_description=task_description,
                human_trajectory=human_trajectory,
                model_trajectory=model_trajectory,
                results_dir=results_dir,
            )

            checkpoint_results[f"checkpoint_{idx}"] = result
            total_score += result["score"]

            status = "achieved" if result["achieved"] else "not achieved"
            logger.info(
                f"Task {task_id}, checkpoint {idx}: {status} (confidence: {result['confidence']:.2f})"
            )

            # Stop evaluating if checkpoint failed
            if not result["achieved"]:
                break

        except Exception as e:
            logger.error(f"Failed to evaluate checkpoint {idx} for task {task_id}: {e}")
            checkpoint_results[f"checkpoint_{idx}"] = {
                "achieved": False,
                "reasoning": f"Evaluation error: {str(e)}",
                "confidence": 0.0,
                "score": 0.0,
            }
            break

    checkpoint_results["total_checkpoint_score"] = round(total_score, 2)
    return checkpoint_results


def _setup_dspy_and_mlflow(results_dir: str, judge_model: str) -> None:
    """Configure DSPy and MLflow for evaluation."""
    lm = dspy.LM(
        f"openai/{judge_model}",
        reasoning_effort="high",
        temperature=1.0,
        max_tokens=120000,
    )
    mlflow.set_tracking_uri("http://127.0.0.1:5000")
    mlflow.set_experiment(f"eval-checkpoints-{Path(results_dir).name}")
    mlflow.dspy.autolog()
    dspy.configure(lm=lm)


def _load_tasks_data() -> Dict[int, Dict[str, Any]]:
    """Load human tasks from tasks.jsonl."""
    tasks_by_id = {}
    with open(DATA_DIR / "tasks.jsonl", "r") as f:
        for line in f:
            if line.strip():
                task = json.loads(line)
                tasks_by_id[task["task_id"]] = task
    return tasks_by_id


def _load_model_results(results_dir: Path) -> Dict[int, Dict[str, Any]]:
    """Load model results from results directory."""
    results_json_dir = results_dir / "results"
    model_tasks_by_id = {}

    for json_file in sorted(results_json_dir.glob("*.json")):
        try:
            with open(json_file, "r") as f:
                model_task = json.load(f)
                model_tasks_by_id[model_task["task_id"]] = model_task
        except Exception as e:
            logger.warning(f"Failed to read {json_file}: {e}")

    return model_tasks_by_id


def _get_failed_task_ids(grade_data: Dict[str, Any], limit: int = None) -> List[str]:
    """Extract failed task IDs from grade data."""
    failed_ids = [
        task_id
        for task_id, result in grade_data["task_results"].items()
        if not result["correct"]
    ]
    return failed_ids[:limit] if limit else failed_ids


def _evaluate_task_wrapper(
    task_id_str: str,
    human_tasks_by_id: Dict[int, Dict],
    model_tasks_by_id: Dict[int, Dict],
    results_dir: Path,
) -> tuple[str, Dict[str, Any]]:
    """Wrapper for parallel task evaluation."""
    task_id = int(task_id_str)
    logger.info(f"Evaluating task {task_id}")

    if task_id not in human_tasks_by_id:
        logger.warning(f"Task {task_id} not found in tasks.jsonl")
        return task_id_str, None

    if task_id not in model_tasks_by_id:
        logger.warning(f"Task {task_id} not found in model results")
        return task_id_str, None

    checkpoint_results = evaluate_checkpoints_for_task(
        task_id=task_id,
        task_data=human_tasks_by_id[task_id],
        model_data=model_tasks_by_id[task_id],
        results_dir=results_dir,
    )

    return task_id_str, checkpoint_results


def _run_parallel_evaluations(
    failed_task_ids: List[str],
    human_tasks_by_id: Dict[int, Dict],
    model_tasks_by_id: Dict[int, Dict],
    results_dir: Path,
) -> Dict[str, Dict[str, Any]]:
    """Run checkpoint evaluations in parallel."""
    checkpoint_evaluations = {}

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
        future_to_task = {
            executor.submit(
                _evaluate_task_wrapper,
                task_id,
                human_tasks_by_id,
                model_tasks_by_id,
                results_dir,
            ): task_id
            for task_id in failed_task_ids
        }

        for future in as_completed(future_to_task):
            task_id, result = future.result()
            if result is not None:
                checkpoint_evaluations[task_id] = result

    return checkpoint_evaluations


def _compute_checkpoint_stats(
    checkpoint_evaluations: Dict[str, Dict],
) -> Dict[str, Any]:
    """Compute statistics from checkpoint evaluations."""
    if not checkpoint_evaluations:
        return {
            "failed_tasks_evaluated": 0,
            "avg_checkpoint_score": 0.0,
            "checkpoint_0_success_rate": 0.0,
            "checkpoint_1_success_rate": 0.0,
            "tasks_with_partial_credit": 0,
        }

    total_evaluated = len(checkpoint_evaluations)

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

    total_scores = [
        r["total_checkpoint_score"] for r in checkpoint_evaluations.values()
    ]
    avg_score = sum(total_scores) / len(total_scores) if total_scores else 0
    tasks_with_partial_credit = sum(1 for score in total_scores if score > 0)

    return {
        "failed_tasks_evaluated": total_evaluated,
        "avg_checkpoint_score": round(avg_score, 3),
        "checkpoint_0_success_rate": round(checkpoint_0_achieved / total_evaluated, 3),
        "checkpoint_1_success_rate": round(checkpoint_1_achieved / total_evaluated, 3),
        "tasks_with_partial_credit": tasks_with_partial_credit,
    }


def _print_summary(checkpoint_stats: Dict[str, Any]) -> None:
    """Print checkpoint evaluation summary."""
    print("\n" + "=" * 50)
    print("CHECKPOINT EVALUATION SUMMARY")
    print("=" * 50)
    print(f"Failed tasks evaluated: {checkpoint_stats['failed_tasks_evaluated']}")
    print(
        f"Average checkpoint score: {checkpoint_stats['avg_checkpoint_score']:.3f} / 0.66"
    )
    print(
        f"Checkpoint 0 success rate: {checkpoint_stats['checkpoint_0_success_rate']:.1%}"
    )
    print(
        f"Checkpoint 1 success rate: {checkpoint_stats['checkpoint_1_success_rate']:.1%}"
    )
    print(f"Tasks with partial credit: {checkpoint_stats['tasks_with_partial_credit']}")
    print("=" * 50)


def evaluate_checkpoints(results_dir: str, judge_model: str):
    """Evaluate checkpoints for all failed tasks and update grade.json."""
    _setup_dspy_and_mlflow(results_dir, judge_model)

    results_path = Path(results_dir)
    grade_json_path = results_path / "grade.json"

    if not grade_json_path.exists():
        logger.error(f"grade.json not found at: {grade_json_path}")
        logger.error("Please run evaluate.py first to generate grade.json")
        return None

    # Load all required data
    with open(grade_json_path, "r") as f:
        grade_data = json.load(f)

    human_tasks_by_id = _load_tasks_data()
    model_tasks_by_id = _load_model_results(results_path)
    failed_task_ids = _get_failed_task_ids(grade_data, limit=10)

    logger.info(f"Found {len(failed_task_ids)} failed tasks to evaluate checkpoints")

    # Run evaluations
    checkpoint_evaluations = _run_parallel_evaluations(
        failed_task_ids,
        human_tasks_by_id,
        model_tasks_by_id,
        results_path,
    )

    # Update grade data with results
    for task_id, checkpoint_result in checkpoint_evaluations.items():
        if task_id in grade_data["task_results"]:
            grade_data["task_results"][task_id][
                "checkpoint_evaluation"
            ] = checkpoint_result

    # Compute and add statistics
    checkpoint_stats = _compute_checkpoint_stats(checkpoint_evaluations)
    grade_data["checkpoint_stats"] = checkpoint_stats

    # Save updated grade.json
    with open(grade_json_path, "w") as f:
        json.dump(grade_data, f, indent=2)

    logger.info(f"Updated grade.json with checkpoint evaluations at {grade_json_path}")
    _print_summary(checkpoint_stats)

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
