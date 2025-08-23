#!/usr/bin/env python3
"""Test the LLM judge function"""

import sys
sys.path.insert(0, '.')

from src.eval.judge import verify_task_completion

# Test case
task = "Find the Display details of new launched iPad pro 11-inch"
response = "The display has Ultra Retina XDR with OLED technology and 1600 nits brightness"
correct_response = "ultra retina, xdr display, oled technology, 1600 nits, pro motion, true tone"

print("Testing LLM Judge...")
print("=" * 50)
print(f"Task: {task}")
print(f"Response: {response}")
print(f"Correct: {correct_response}")
print("\nCalling judge...")

try:
    result = verify_task_completion(task, response, correct_response)
    
    print("\nJudge Result:")
    print(f"Correct: {result['correct']}")
    print(f"Confidence: {result['confidence']}%")
    print(f"Reasoning: {result['reasoning']}")
    
except Exception as e:
    print(f"Error: {e}")
    print("Make sure you have OPENAI_API_KEY or appropriate API keys set")