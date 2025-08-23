import asyncio
import json
import logging

from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from dotenv import load_dotenv
from browser_use import Agent, ChatOpenAI

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ActionMapping:
    """Maps between Browser-Use actions and our golden trajectory format"""
    agent_action: str
    trajectory_action: str
    key_mapping: Dict[str, str]


# Define action mappings between Browser-Use and our format
ACTION_MAPPINGS = {
    "go_to_url": ActionMapping(
        agent_action="go_to_url",
        trajectory_action="go_to",
        key_mapping={"url": "url"}
    ),
    "input_text": ActionMapping(
        agent_action="input_text",
        trajectory_action="type",
        key_mapping={"text": "text"}
    ),
    "click_element_by_index": ActionMapping(
        agent_action="click_element_by_index",
        trajectory_action="click",
        key_mapping={}  # Will need special handling for selector
    ),
}


def map_agent_action_to_trajectory(agent_action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a Browser-Use agent action to our trajectory format"""
    # Get the action type (first key in the action dict)
    action_type = next(iter(agent_action.keys()))
    
    if action_type not in ACTION_MAPPINGS:
        return None
    
    mapping = ACTION_MAPPINGS[action_type]
    trajectory_action = {
        "type": mapping.trajectory_action,
        "params": {}
    }
    
    # Map parameters
    if action_type == "go_to_url":
        trajectory_action["params"]["url"] = agent_action[action_type]["url"]
    
    elif action_type == "input_text":
        trajectory_action["params"]["text"] = agent_action[action_type]["text"]
        # Try to get selector from interacted_element
        if agent_action.get("interacted_element"):
            element = agent_action["interacted_element"]
            # Create selector from element attributes
            if hasattr(element, "attributes"):
                attrs = element.attributes
                if attrs.get("id"):
                    trajectory_action["params"]["selector"] = f"#{attrs['id']}"
                elif attrs.get("class"):
                    trajectory_action["params"]["selector"] = f".{attrs['class'].replace(' ', '.')}"
                else:
                    trajectory_action["params"]["selector"] = element.node_name.lower()
    
    elif action_type == "click_element_by_index":
        # For clicks, try to extract selector from interacted_element
        if agent_action.get("interacted_element"):
            element = agent_action["interacted_element"]
            if hasattr(element, "attributes"):
                attrs = element.attributes
                if attrs.get("id"):
                    trajectory_action["params"]["selector"] = f"#{attrs['id']}"
                elif attrs.get("class"):
                    trajectory_action["params"]["selector"] = f".{attrs['class'].replace(' ', '.')}"
                else:
                    trajectory_action["params"]["selector"] = element.node_name.lower()
    
    return trajectory_action


def compare_actions(agent_action: Dict[str, Any], golden_action: Dict[str, Any]) -> float:
    """Compare an agent action with a golden trajectory action, return similarity score (0-1)"""
    # Check if action types match
    if agent_action.get("type") != golden_action.get("type"):
        return 0.0
    
    action_type = agent_action["type"]
    
    if action_type == "go_to":
        # For navigation, URLs should match
        agent_url = agent_action.get("params", {}).get("url", "")
        golden_url = golden_action.get("params", {}).get("url", "")
        
        # Normalize URLs (remove trailing slashes, etc.)
        agent_url = agent_url.rstrip("/").lower()
        golden_url = golden_url.rstrip("/").lower()
        
        return 1.0 if agent_url == golden_url else 0.0
    
    elif action_type == "type":
        # For typing, text should match
        agent_text = agent_action.get("params", {}).get("text", "")
        golden_text = golden_action.get("params", {}).get("text", "")
        
        # Calculate text similarity
        if agent_text == golden_text:
            return 1.0
        elif agent_text.lower() == golden_text.lower():
            return 0.9
        elif golden_text in agent_text or agent_text in golden_text:
            return 0.7
        else:
            return 0.0
    
    elif action_type == "click":
        # For clicks, check if selectors match or are similar
        agent_selector = agent_action.get("params", {}).get("selector", "")
        golden_selector = golden_action.get("params", {}).get("selector", "")
        
        if agent_selector == golden_selector:
            return 1.0
        
        # Check if they reference the same ID
        if agent_selector.startswith("#") and golden_selector.startswith("#"):
            return 1.0 if agent_selector == golden_selector else 0.0
        
        # Partial match for class selectors
        if "." in agent_selector and "." in golden_selector:
            agent_classes = set(agent_selector.split("."))
            golden_classes = set(golden_selector.split("."))
            intersection = agent_classes & golden_classes
            if intersection:
                return len(intersection) / max(len(agent_classes), len(golden_classes))
        
        return 0.3  # Partial credit if action type matches but selector doesn't
    
    return 0.0


def evaluate_task_execution(
    agent_actions: List[Dict[str, Any]], 
    golden_trajectory: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Evaluate how well the agent's actions match the golden trajectory
    
    Key considerations:
    - Agent may take more steps than golden trajectory (including mistakes/corrections)
    - Agent might try different approaches before succeeding
    - Order matters but we allow flexibility for exploration
    - We focus on whether key golden steps were eventually completed
    """
    
    # Convert agent actions to our format
    converted_actions = []
    for action in agent_actions:
        # Skip non-action entries like 'done' or 'extract_structured_data'
        action_type = next(iter(action.keys()))
        if action_type in ["done", "extract_structured_data"]:
            continue
        
        converted = map_agent_action_to_trajectory(action)
        if converted:
            converted_actions.append(converted)
    
    # Calculate metrics
    total_golden_steps = len(golden_trajectory)
    total_agent_steps = len(converted_actions)
    correct_steps = 0
    partial_credit = 0.0
    
    # Track which golden steps were matched and at what position
    matched_golden = {}  # golden_idx -> (agent_idx, score)
    extra_agent_steps = 0
    
    # For each golden step, find the best matching agent action
    for golden_idx, golden_action in enumerate(golden_trajectory):
        best_match_score = 0.0
        best_match_agent_idx = -1
        
        # Look through all agent actions to find best match
        # This allows agent to take extra steps before achieving the golden step
        for agent_idx, agent_action in enumerate(converted_actions):
            score = compare_actions(agent_action, golden_action)
            
            # Prefer matches that maintain relative order
            # Give slight penalty if agent action comes much later than expected
            position_diff = abs(agent_idx / max(len(converted_actions), 1) - 
                              golden_idx / max(len(golden_trajectory), 1))
            order_penalty = 1.0 - (position_diff * 0.1)  # Max 10% penalty for order
            adjusted_score = score * max(order_penalty, 0.9)
            
            if adjusted_score > best_match_score:
                best_match_score = adjusted_score
                best_match_agent_idx = agent_idx
        
        if best_match_agent_idx >= 0 and best_match_score > 0.5:
            matched_golden[golden_idx] = (best_match_agent_idx, best_match_score)
            if best_match_score >= 0.9:
                correct_steps += 1
            partial_credit += best_match_score
    
    # Count agent steps that didn't match any golden step (extra/exploratory steps)
    matched_agent_indices = {idx for idx, _ in matched_golden.values()}
    extra_agent_steps = len([i for i in range(len(converted_actions)) 
                           if i not in matched_agent_indices])
    
    # Check if final result is correct (task completed)
    final_result_correct = False
    if agent_actions:
        # Check for explicit success signal
        if "done" in agent_actions[-1]:
            done_action = agent_actions[-1]["done"]
            final_result_correct = done_action.get("success", False)
        
        # If no explicit signal but all golden steps were matched with high confidence
        elif len(matched_golden) == len(golden_trajectory) and partial_credit >= len(golden_trajectory) * 0.8:
            final_result_correct = True
    
    # Calculate overall accuracy (based on golden steps completed)
    accuracy = partial_credit / total_golden_steps if total_golden_steps > 0 else 0.0
    
    # Calculate efficiency metric (penalizes too many extra steps)
    efficiency = len(matched_golden) / total_agent_steps if total_agent_steps > 0 else 0.0
    
    return {
        "total_golden_steps": total_golden_steps,
        "total_agent_steps": total_agent_steps,
        "correct_steps": correct_steps,
        "partial_credit": partial_credit,
        "accuracy": accuracy,
        "final_result_correct": final_result_correct,
        "agent_actions": converted_actions,
        "matched_steps": len(matched_golden),
        "extra_steps": extra_agent_steps,
        "efficiency": efficiency,
        "matched_golden_indices": list(matched_golden.keys())
    }


async def run_task_with_agent(task_description: str, model: str = "gpt-5-2025-08-07") -> Dict[str, Any]:
    """Run a single task with the Browser-Use agent"""
    try:
        llm = ChatOpenAI(model=model, temperature=0.0)
        agent = Agent(
            task=task_description,
            llm=llm,
            verbose=False,
            max_steps=50  # Reasonable limit
        )
        
        # Run the agent
        history = await agent.run()
        
        # Get the model actions
        model_actions = history.model_actions()
        
        return {
            "success": True,
            "actions": model_actions,
            "error": None
        }
    
    except Exception as e:
        logger.error(f"Error running task: {e}")
        return {
            "success": False,
            "actions": [],
            "error": str(e)
        }


async def process_all_tasks(model: str = "gpt-4o-mini"):
    """Process all tasks from tasks.jsonl and evaluate results"""
    
    # Load tasks
    tasks_file = Path("data/tasks.jsonl")
    if not tasks_file.exists():
        logger.error(f"Tasks file not found: {tasks_file}")
        return
    
    tasks = []
    with open(tasks_file, 'r') as f:
        for line in f:
            if line.strip():
                tasks.append(json.loads(line))
    
    logger.info(f"Loaded {len(tasks)} tasks")
    
    # Process each task
    results = []
    for i, task in enumerate(tasks):
        logger.info(f"Processing task {i+1}/{len(tasks)}: {task['task_description']}")
        
        # Run the agent
        agent_result = await run_task_with_agent(task["task_description"], model)
        
        if agent_result["success"]:
            # Evaluate against golden trajectory
            evaluation = evaluate_task_execution(
                agent_result["actions"], 
                task["tool_calls"]
            )
            
            result = {
                "task_id": task["task_id"],
                "task_description": task["task_description"],
                "model": model,
                "success": True,
                "total_golden_steps": evaluation["total_golden_steps"],
                "total_agent_steps": evaluation["total_agent_steps"],
                "correct_steps": evaluation["correct_steps"],
                "matched_steps": evaluation["matched_steps"],
                "extra_steps": evaluation["extra_steps"],
                "accuracy": evaluation["accuracy"],
                "efficiency": evaluation["efficiency"],
                "final_result_correct": evaluation["final_result_correct"],
                "agent_actions": evaluation["agent_actions"],
                "matched_golden_indices": evaluation["matched_golden_indices"],
                "error": None
            }
        else:
            result = {
                "task_id": task["task_id"],
                "task_description": task["task_description"],
                "model": model,
                "success": False,
                "total_golden_steps": len(task["tool_calls"]),
                "total_agent_steps": 0,
                "correct_steps": 0,
                "matched_steps": 0,
                "accuracy": 0.0,
                "final_result_correct": False,
                "agent_actions": [],
                "error": agent_result["error"]
            }
        
        results.append(result)
        logger.info(f"Task {task['task_id']} - Accuracy: {result['accuracy']:.2%}, " +
                   f"Steps: {result['matched_steps']}/{result['total_golden_steps']}, " +
                   f"Extra steps: {result.get('extra_steps', 0)}")
    
    # Save results
    output_dir = Path("src/eval/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / f"browseruse-{model.replace('/', '-')}.jsonl"
    with open(output_file, 'w') as f:
        for result in results:
            f.write(json.dumps(result) + '\n')
    
    logger.info(f"Results saved to {output_file}")
    
    # Print summary
    successful_tasks = sum(1 for r in results if r["success"])
    avg_accuracy = sum(r["accuracy"] for r in results) / len(results) if results else 0
    avg_efficiency = sum(r.get("efficiency", 0) for r in results if r["success"]) / successful_tasks if successful_tasks > 0 else 0
    correct_finals = sum(1 for r in results if r["final_result_correct"])
    total_extra_steps = sum(r.get("extra_steps", 0) for r in results if r["success"])
    
    print("\n=== Summary ===")
    print(f"Total tasks: {len(tasks)}")
    print(f"Successfully executed: {successful_tasks}/{len(tasks)}")
    print(f"Average accuracy: {avg_accuracy:.2%}")
    print(f"Average efficiency: {avg_efficiency:.2%}")
    print(f"Tasks with correct final result: {correct_finals}/{len(tasks)}")
    print(f"Total extra/exploratory steps across all tasks: {total_extra_steps}")


async def main():
    """Main entry point"""
    # You can change the model here
    model = "gpt-4o-mini"  # or "gpt-4o", "gpt-3.5-turbo", etc.
    
    await process_all_tasks(model)


if __name__ == "__main__":
    asyncio.run(main())
