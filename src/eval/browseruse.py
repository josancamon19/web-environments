import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from browser_use import Agent, ChatOpenAI

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run_task_with_agent(
    task: Dict[str, Any], model: str = "gpt-5-2025-08-07"
) -> Dict[str, Any]:
    """Run a single task with the Browser-Use agent and capture all data"""
    start_time = datetime.now()
    llm = ChatOpenAI(model=model, temperature=0.0)
    agent = Agent(task=task["task_description"], llm=llm, verbose=True, max_steps=20)
    history = await agent.run()
    duration = (datetime.now() - start_time).total_seconds()

    return {
        "task_id": task["task_id"],
        "task_description": task["task_description"],
        "task_type": task.get("task_type", "unknown"),
        "success": True,
        "duration_seconds": duration,
        "action_count": len(history.model_actions()),
        "full_history": history.model_dump(),
    }


async def process_all_tasks(model: str):
    with open(Path("data/tasks.jsonl"), "r") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    logger.info(f"Loaded {len(tasks)} tasks")
    results = []
    for i, task in enumerate(tasks):
        logger.info(
            f"Processing task {i + 1}/{len(tasks)}: {task['task_description'][:100]}..."
        )
        result = await run_task_with_agent(task, model)
        results.append(result)
        logger.info(
            f"Task {task['task_id']} - Success: {result['success']}, "
            f"Actions: {result['action_count']}, "
            f"Duration: {result['duration_seconds']:.2f}s"
        )

        save_results(results, model)
    return save_results(results, model)


def save_results(results: List[Dict[str, Any]], model: str) -> Path:
    """Save results to a JSON file with all captured data"""
    output_dir = Path("src/eval/results")
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = (
        output_dir / f"browseruse_data_{model.replace('/', '-')}_{timestamp}.json"
    )

    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    logger.info(f"Results saved to {output_file}")
    return output_file


async def main():
    output_file = await process_all_tasks("o3-2025-04-16")
    print(f"\nFull data saved to: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
    # TODO completion eval ~ info = str/llm judge, action = dom/final state, llm judge
