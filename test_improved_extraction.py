#!/usr/bin/env python3
"""Test improved tool call extraction from browser-use history"""

import json
import sys
sys.path.insert(0, '.')

from src.eval.browseruse import extract_tool_calls

# Load the browseruse data  
with open('src/eval/results/browseruse_data_o3-2025-04-16_20250823_122120.json') as f:
    data = json.load(f)

# Extract tool calls using the improved function
if data and 'history' in data[0]:
    history = data[0]['history']
    tool_calls = extract_tool_calls(history)
    
    print('Improved tool call extraction:')
    print('=' * 50)
    for i, tc in enumerate(tool_calls, 1):
        print(f'\n{i}. Type: {tc["type"]}')
        params = tc.get("params", {})
        if 'query' in params:
            print(f'   Query: {params["query"]}')
        if 'selector' in params:
            print(f'   Selector: {params["selector"]}')
        if 'coordinates' in params:
            print(f'   Coordinates: x={params["coordinates"]["x"]}, y={params["coordinates"]["y"]}')
        if 'element_details' in params:
            details = params["element_details"]
            print(f'   Element: <{details["node_name"]}>')
            if 'href' in details.get("attributes", {}):
                print(f'   Href: {details["attributes"]["href"]}')
        if 'direction' in params:
            print(f'   Direction: {params["direction"]}')
        if 'pages' in params:
            print(f'   Pages: {params["pages"]}')
    
    print(f'\n\nTotal tool calls extracted: {len(tool_calls)}')
else:
    print('No history found in data')