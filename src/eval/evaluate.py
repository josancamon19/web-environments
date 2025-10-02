import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

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
    output_file = Path("results") / f"browseruse-{model.replace('/', '-')}.jsonl"
    print(output_file)
    if not output_file.exists():
        logger.error(f"Output file not found: {output_file}")
        return None

    # Load the original tasks to get correct answers
    human_tasks_by_id = {}
    with open(Path(f"{DATA_DIR}/tasks.jsonl"), "r") as f:
        for line in f:
            if not line.strip():
                continue
            task = json.loads(line)
            human_tasks_by_id[task["task_id"]] = task

    # Evaluate each result
    model_tasks = []
    with open(output_file, "r") as f:
        for line in f:
            if not line.strip():
                continue
            model_tasks.append(json.loads(line))

    def _get_model_completion_step(model_task: Dict[str, Any]) -> Dict[str, Any]:
        for step in model_task["dump"]:
            done = step["model_output"].get("action", [{}])[-1].get("done")
            if done:
                del step["result"]
                return step

    evaluations = {}
    for model_task in model_tasks:
        task_id = model_task["task_id"]

        if model_task.get("task_type") != "information_retrieval":
            logger.info(f"Skipping task {task_id} (not information retrieval)")
            continue

        model_completion_step = _get_model_completion_step(model_task)
        if not model_completion_step:
            logger.warning(f"No final step found for task {task_id}")
            evaluations[task_id] = {
                "correct": False,
                "reasoning": "No final step found",
                "confidence": 0,
            }
            continue

        model_trajectory = model_task["tool_calls"]
        model_last_dom = model_task["step_dom_mapping"][str(len(model_trajectory))]
        model_last_dom = open(Path("results") / model_last_dom, "r").read()
        # ===
        human_task = human_tasks_by_id[task_id]
        human_trajectory = human_task["tool_calls"]
        human_last_dom = human_trajectory[-1]["params"]["dom_state"]
        human_last_dom = open(Path("data/dev") / human_last_dom, "r").read()
        human_answer = human_task["answer"]
        # ===
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
        evaluations[task_id] = {
            "correct": result.correct,
            "reasoning": result.reasoning,
            "confidence": result.confidence,
        }

    print(json.dumps(evaluations, indent=2))
    return evaluations


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
        sys.exit(1)


if __name__ == "__main__":
    main()
    # TODO: improve db to jsonl format code, is shit.
    # TODO: ask for website for the task (?)
    # TODO: would bounding boxes help?
    # TODO: GUI is kinda trash

    # ----

    # 1. harness + browseruse works
    # 2. openai cua setup agent eval
    # 3. quick mvp claude cua + issue
    # 4. data directories refactor
    # 5. checkpoint based evaluation if task completion failed. Agent based.
