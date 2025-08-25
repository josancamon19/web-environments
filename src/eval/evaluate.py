#!/usr/bin/env python3
"""
Standalone script to evaluate model outputs using the LLM judge.
Usage: python evaluate_outputs.py <model_name>
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any
from litellm import completion

# Add src to path
sys.path.insert(0, ".")


# customized HLE prompt
prompt = """
Judge whether the following [response] to [browser_task] is correct or not based on the precise and unambiguous [correct_response] below.

[browser_task]: {task}
[response]: {response}

Your judgement must be in json format and criteria specified below:
extracted_final_answer: The final exact answer extracted from the [response]. Put the extracted answer as 'None' if there is no exact, final answer to extract from the response.

[correct_response]: {correct_response}

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_response], focusing only on if there are meaningful differences between [correct_response] and the extracted_final_answer. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_response], focus only on whether the answers match.
correct: Answer 'yes' if extracted_final_answer matches the [correct_response] given above, or is within a small margin of error for numerical problems, or includes extra details not found in [correct_response] but not necessarily wrong given [browser_task]. Answer 'no' otherwise, i.e. if there if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.
confidence: The extracted confidence score between 0% and 100% from [response]. Put 100 if there is no confidence score available.
"""


def verify_task_completion(
    task: str, response: str, correct_response: str, model: str = "gpt-4.1-2025-04-14"
) -> Dict[str, Any]:
    formatted_prompt = prompt.format(
        task=task, response=response, correct_response=correct_response
    )
    llm_response = completion(
        model=model,
        messages=[{"role": "user", "content": formatted_prompt}],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    result = json.loads(llm_response.choices[0].message.content)
    return {
        "reasoning": result.get("reasoning", ""),
        "correct": result.get("correct", "").lower() == "yes",
        "confidence": int(str(result.get("confidence", 100)).rstrip("%")),
    }


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def evaluate_model_outputs(model: str, judge_model: str = "gpt-4.1-2025-04-14"):
    output_file = (
        Path("src/eval/results") / f"browseruse-{model.replace('/', '-')}.jsonl"
    )
    if not output_file.exists():
        logger.error(f"Output file not found: {output_file}")
        return None

    # Load the original tasks to get correct answers
    tasks_by_id = {}
    with open(Path("data/tasks.jsonl"), "r") as f:
        for line in f:
            if line.strip():
                task = json.loads(line)
                tasks_by_id[task["task_id"]] = task

    # Evaluate each result
    evaluation_results = []
    with open(output_file, "r") as f:
        for line in f:
            if line.strip():
                result = json.loads(line)
                task_id = result["task_id"]

                # Skip if not an information retrieval task
                if result.get("task_type") != "information_retrieval":
                    logger.info(f"Skipping task {task_id} (not information retrieval)")
                    continue

                # Get the correct answer from original task
                if task_id not in tasks_by_id:
                    logger.warning(f"Task {task_id} not found in original tasks")
                    continue

                original_task = tasks_by_id[task_id]
                correct_answer = original_task.get("answer", "")

                # Get the model's answer
                model_answer = result.get("answer", "")

                if not model_answer:
                    logger.warning(f"No answer found for task {task_id}")
                    eval_result = {
                        "task_id": task_id,
                        "task_description": result["task_description"],
                        "correct": False,
                        "reasoning": "No answer provided by model",
                        "confidence": 0,
                    }
                else:
                    # Call the judge
                    logger.info(f"Evaluating task {task_id}...")
                    try:
                        judge_result = verify_task_completion(
                            task=result["task_description"],
                            response=model_answer,
                            correct_response=correct_answer,
                            model=judge_model,
                        )

                        eval_result = {
                            "task_id": task_id,
                            "task_description": result["task_description"],
                            "model_answer": model_answer,
                            "correct_answer": correct_answer,
                            **judge_result,
                        }
                    except Exception as e:
                        logger.error(f"Judge failed for task {task_id}: {e}")
                        eval_result = {
                            "task_id": task_id,
                            "task_description": result["task_description"],
                            "correct": False,
                            "reasoning": f"Judge error: {str(e)}",
                            "confidence": 0,
                        }

                evaluation_results.append(eval_result)

    # Calculate statistics
    if evaluation_results:
        correct_count = sum(1 for r in evaluation_results if r["correct"])
        total_count = len(evaluation_results)
        accuracy = correct_count / total_count * 100

        logger.info(f"\nEvaluation Results for {model}:")
        logger.info(f"Total tasks evaluated: {total_count}")
        logger.info(f"Correct: {correct_count}")
        logger.info(f"Accuracy: {accuracy:.2f}%")

        # Save evaluation results
        eval_output_file = (
            Path("src/eval/results") / f"evaluation-{model.replace('/', '-')}.json"
        )
        with open(eval_output_file, "w") as f:
            json.dump(
                {
                    "model": model,
                    "judge_model": judge_model,
                    "accuracy": accuracy,
                    "correct_count": correct_count,
                    "total_count": total_count,
                    "evaluations": evaluation_results,
                },
                f,
                indent=2,
            )

        logger.info(f"Evaluation results saved to {eval_output_file}")

        return {
            "accuracy": accuracy,
            "correct_count": correct_count,
            "total_count": total_count,
            "results": evaluation_results,
        }
    else:
        logger.warning("No tasks were evaluated")
        return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python evaluate_outputs.py <model_name> [judge_model]")
        print("Example: python evaluate_outputs.py o3-2025-04-16")
        print("Example: python evaluate_outputs.py o3-2025-04-16 gpt-4.1-2025-04-14")
        sys.exit(1)

    model = sys.argv[1]
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
