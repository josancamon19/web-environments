from typing import Any
import dspy

lm = dspy.LM(
    "openai/gpt-5-nano",
    reasoning_effort="minimal",
    temperature=1.0,
    max_tokens=64000,
)
dspy.configure(lm=lm)


class JudgeCompletion(dspy.Signature):
    # TODO: improve prompt, provide more examples, use gpt prompt optimizer
    """
    You will be given a target request, and a list of candidates that we want to match the target request to.
    The current request comes from a current browser replaying a human trajectory, and the candidates are HAR entries that are similar to the target request.
    Your task is to identify the best candidate that matches the target request, the mismatching parts are the query parameters and the POST data generally on parameters that are dynamic and change frequently.
    """

    target_request: dict[str, Any] = dspy.InputField(description="The target request")
    post_data: str = dspy.InputField(
        description="The POST data of the target request, only if it is a POST request",
        default=None,
    )
    candidates: list[dict[str, Any]] = dspy.InputField(description="The candidates")

    selected_match: int = dspy.OutputField(
        description="The index of the selected match"
    )
    reasoning: str = dspy.OutputField(
        description="The reasoning for the selected match"
    )
    confidence: float = dspy.OutputField(
        description="The confidence score for the selected match"
    )


def get_lm_match(
    target_request: dict[str, Any],
    post_data: str | None,
    candidates: list[dict[str, Any]],
) -> int:
    completion = dspy.Predict(JudgeCompletion)
    result = completion(
        target_request=target_request,
        post_data=post_data,
        candidates=candidates,
    )
    print("🔍 LM match result:", result)
    return result.selected_match
