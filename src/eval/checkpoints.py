import json
import logging
from pathlib import Path
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import dspy
import typer
from tasks.db_to_jsonl_format import BaseToolCallData

import sys
import os
import mlflow

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config.storage_config import DATA_DIR


# TODO: requires to be a dspy.Agent
class JudgeCheckpoint(dspy.Signature):
    """
    Judge if a model achieved a specific checkpoint goal in a browser task.

    The checkpoint represents an intermediate milestone toward completing the task.
    You should evaluate whether the model's actions and the state of the browser
    (as reflected in the DOMs) indicate that this checkpoint goal was achieved.

    The model may take different actions than the human, but if it achieves the
    same intermediate goal described in the checkpoint reasoning, it should be
    considered successful.
    """

    task: str = dspy.InputField(description="The full task description")
    checkpoint_index: int = dspy.InputField(description="Checkpoint number (0 or 1)")
    checkpoint_reasoning: str = dspy.InputField(
        description="Description of what this checkpoint represents"
    )
    human_checkpoint_tool_call_index: int = dspy.InputField(
        description="The tool call index in human trajectory where this checkpoint was reached"
    )

    agent_trajectory: List[BaseToolCallData] = dspy.InputField(
        description="The model's complete trajectory of actions"
    )
    agent_doms_summary: str = dspy.InputField(
        description="Summary of key DOMs the model accessed"
    )

    achieved: bool = dspy.OutputField(
        description="Whether the model achieved this checkpoint goal"
    )
    reasoning: str = dspy.OutputField(
        description="Explanation of why the checkpoint was or wasn't achieved"
    )
    confidence: float = dspy.OutputField(
        description="Confidence score (0-1) for this judgment"
    )


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def evaluate_single_checkpoint(
    task_id: int,
    checkpoint_idx: int,
    checkpoint_tool_idx: int,
    checkpoint_reasoning: str,
    task_description: str,
    model_trajectory: List[BaseToolCallData],
    model_doms: Dict[str, str],
    results_dir: Path,
) -> Dict[str, Any]:
    """Evaluate a single checkpoint for a task."""

    # Create a summary of DOMs (to avoid token limits)
    # We'll use the first and last few DOMs as a representative sample
    dom_summary_parts = []
    dom_steps = sorted([int(k) for k in model_doms.keys()])

    # Sample some DOMs to avoid token limits
    sample_steps = []
    if len(dom_steps) > 0:
        sample_steps.append(dom_steps[0])  # First
        if len(dom_steps) > 1:
            sample_steps.append(dom_steps[-1])  # Last
        if len(dom_steps) > 2:
            mid = len(dom_steps) // 2
            sample_steps.append(dom_steps[mid])  # Middle

    for step in sorted(set(sample_steps)):
        dom_content = model_doms[str(step)]
        # Truncate each DOM to avoid excessive tokens
        truncated = (
            dom_content[:2000] + "..." if len(dom_content) > 2000 else dom_content
        )
        dom_summary_parts.append(f"Step {step} DOM preview:\n{truncated}")

    doms_summary = "\n\n".join(dom_summary_parts)

    judge = dspy.Predict(JudgeCheckpoint)
    result = judge(
        task=task_description,
        checkpoint_index=checkpoint_idx,
        checkpoint_reasoning=checkpoint_reasoning,
        human_checkpoint_tool_call_index=checkpoint_tool_idx,
        agent_trajectory=model_trajectory,
        agent_doms_summary=doms_summary,
    )

    score = 0.33 if result.achieved else 0.0

    return {
        "achieved": result.achieved,
        "reasoning": result.reasoning,
        "confidence": result.confidence,
        "score": score,
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

    if len(checkpoints) != 2 or len(checkpoints_reasoning) != 2:
        logger.warning(f"Task {task_id} does not have exactly 2 checkpoints, skipping")
        return None

    task_description = task_data["task_description"]
    model_trajectory = model_data["tool_calls"]

    # Load model DOMs
    step_dom_mapping = model_data.get("step_dom_mapping", {})
    model_doms = {}
    for step, dom_path in step_dom_mapping.items():
        try:
            full_path = results_dir / dom_path
            if full_path.exists():
                model_doms[step] = open(full_path, "r").read()
        except Exception as e:
            logger.warning(f"Failed to load DOM for task {task_id}, step {step}: {e}")

    # Evaluate each checkpoint
    checkpoint_results = {}
    total_score = 0.0

    for idx in range(2):
        checkpoint_tool_idx = checkpoints[idx]
        checkpoint_reason = checkpoints_reasoning[idx]

        logger.info(f"Evaluating task {task_id}, checkpoint {idx}...")

        try:
            result = evaluate_single_checkpoint(
                task_id=task_id,
                checkpoint_idx=idx,
                checkpoint_tool_idx=checkpoint_tool_idx,
                checkpoint_reasoning=checkpoint_reason,
                task_description=task_description,
                model_trajectory=model_trajectory,
                model_doms=model_doms,
                results_dir=results_dir,
            )

            checkpoint_results[f"checkpoint_{idx}"] = result
            total_score += result["score"]

            logger.info(
                f"Task {task_id}, checkpoint {idx}: "
                f"{'achieved' if result['achieved'] else 'not achieved'} "
                f"(confidence: {result['confidence']:.2f})"
            )
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
        max_tokens=64000,
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
    ]

    logger.info(f"Found {len(failed_task_ids)} failed tasks to evaluate checkpoints")

    def evaluate_task_wrapper(task_id_str):
        """Wrapper for parallel execution."""
        task_id = int(task_id_str)

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
