# https://github.com/bytedance/UI-TARS/blob/main/README_deploy.md

import os
import re
import json
from dotenv import load_dotenv
from openai import OpenAI


def add_box_token(input_string):
    # Step 1: Split the string into individual actions
    if "Action: " in input_string and "start_box=" in input_string:
        suffix = input_string.split("Action: ")[0] + "Action: "
        actions = input_string.split("Action: ")[1:]
        processed_actions = []
        for action in actions:
            action = action.strip()
            # Step 2: Extract coordinates (start_box or end_box) using regex
            coordinates = re.findall(
                r"(start_box|end_box)='\((\d+),\s*(\d+)\)'", action
            )

            updated_action = action  # Start with the original action
            for coord_type, x, y in coordinates:
                # Convert x and y to integers
                updated_action = updated_action.replace(
                    f"{coord_type}='({x},{y})'",
                    f"{coord_type}='<|box_start|>({x},{y})<|box_end|>'",
                )
            processed_actions.append(updated_action)

        # Step 5: Reconstruct the final string
        final_string = suffix + "\n\n".join(processed_actions)
    else:
        final_string = input_string
    return final_string


# Could you help me set the image to Palette-Based?

load_dotenv()
client = OpenAI(
    base_url="https://ojk4ddtpo1ham9fx.us-east-1.aws.endpoints.huggingface.cloud/v1/",
    api_key=os.getenv("HF_TOKEN"),
)

result = {}
messages = json.load(open("./src/eval/tars/test_messages.json"))
for message in messages:
    if message["role"] == "assistant":
        message["content"] = add_box_token(message["content"])
        print(message["content"])

chat_completion = client.chat.completions.create(
    model="tgi",
    messages=messages,
    top_p=None,
    temperature=0.0,
    max_tokens=400,
    stream=True,
    seed=None,
    stop=None,
    frequency_penalty=None,
    presence_penalty=None,
)

response = ""
for message in chat_completion:
    response += message.choices[0].delta.content
print(response)
