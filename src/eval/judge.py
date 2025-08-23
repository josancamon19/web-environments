import json
from typing import Dict, Any
from litellm import completion

# customized HLE prompt
prompt = """
Judge whether the following [response] to [browser_task] is correct or not based on the precise and unambiguous [correct_response] below.

[browser_task]: {task}
[response]: {response}

Your judgement must be in the format and criteria specified below:
extracted_final_answer: The final exact answer extracted from the [response]. Put the extracted answer as 'None' if there is no exact, final answer to extract from the response.

[correct_response]: {correct_response}

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_response], focusing only on if there are meaningful differences between [correct_response] and the extracted_final_answer. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_response], focus only on whether the answers match.
correct: Answer 'yes' if extracted_final_answer matches the [correct_response] given above, or is within a small margin of error for numerical problems. Answer 'no' otherwise, i.e. if there if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.
confidence: The extracted confidence score between 0% and 100% from [response]. Put 100 if there is no confidence score available.
"""


def verify_task_completion(
    task: str, response: str, correct_response: str, model: str = "gpt-4o-mini"
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
    print(verify_task_completion)
    return {
        "reasoning": result.get("reasoning", ""),
        "correct": result.get("correct", "").lower() == "yes",
        "confidence": int(str(result.get("confidence", 100)).rstrip("%")),
    }
