from dspy.predict.react import ReAct

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, List

import dspy
import mlflow

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.scripts.postprocessing.db_to_jsonl_format import BaseToolCallData  # noqa: E402


# Disable MLflow logging to avoid spam warnings
os.environ["MLFLOW_TRACKING_URI"] = ""
mlflow.set_tracking_uri("")
logging.getLogger("mlflow").setLevel(logging.ERROR)


class JudgeCompletion(dspy.Signature):
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


class JudgeCheckpoint(dspy.Signature):
    """
    Your task is to judge wether the model achieved an intermediate goal in a task.
    You will be given a task description and a human trajectory, representing the actions the human took to complete the task.

    We know that the model didnt complete the full task, so we want to consider partial points depending if the model achieved an intermediate goal.
    You will be also given the agent's trajectory representing the actions the agent took attempting to complete the task.

    Checkpoint index, represents the human trajectory index that the checkpoint represents, and checkpoint reasoning, is a description of what this checkpoint represents.

    Your task is given this representation of an intermediate goal, and you need to judge wether the model achieved this goal in its trajectory.
    """

    task_id: int = dspy.InputField(description="The task id")
    task_description: str = dspy.InputField(description="The full task description")
    human_trajectory: List[BaseToolCallData] = dspy.InputField(
        description="The human's complete trajectory of actions"
    )
    agent_trajectory: List[BaseToolCallData] = dspy.InputField(
        description="The agent's complete trajectory of actions"
    )
    agent_doms_available: List[int] = dspy.InputField(
        description="The available list of steps id that include DOM details"
    )

    checkpoint_index: int = dspy.InputField(description="Checkpoint number (0 or 1)")
    checkpoint_reasoning: str = dspy.InputField(
        description="Description of what this checkpoint represents and why it is important"
    )

    achieved: bool = dspy.OutputField(
        description="Whether the agent achieved this intermediate goal"
    )
    reasoning: str = dspy.OutputField(
        description="Explanation of why the intermediate goal was or wasn't achieved"
    )
    confidence: float = dspy.OutputField(
        description="Confidence score (0-1) for your judgment"
    )


def get_lm_judge(results_dir: Path) -> dspy.ReAct:
    def get_agent_full_step_detail(task_id: int, step_idx: int) -> dict:
        """Get the full step detail for a given task and step index."""
        with open(results_dir / f"results/{task_id}.json", "r") as f:
            task = json.load(f)
        if len(task["dump"]) <= step_idx:
            return {"error": f"Step {step_idx} detail not found in task {task_id} dump"}
        return task["dump"][step_idx]

    def get_dom_details(task_id: int, step_idx: int) -> str:
        """Get the DOM details for a given task and step index."""
        try:
            with open(results_dir / f"doms/{task_id}/step_{step_idx}.txt", "r") as f:
                return (
                    f.read()
                    if f.read()
                    else f"DOM details not found for task {task_id}, step {step_idx}"
                )
        except Exception as _:
            return f"DOM details not found for task {task_id}, step {step_idx}"

    # ======= TOOLS AVAILABLE =======

    judge: ReAct = dspy.ReAct(
        JudgeCheckpoint, tools=[get_agent_full_step_detail, get_dom_details]
    )

    return judge
