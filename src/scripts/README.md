---
task_categories:
- reinforcement-learning
- question-answering
language:
- en
size_categories:
- n<1K
tags:
- web-agents
- browser-automation
- human-demonstrations
- web-navigation
- multi-step-reasoning
license: mit
---

# Mind2Web Subset - Human Demonstrations

A collection of human-demonstrated web navigation tasks with detailed interaction traces. This dataset captures real browser interactions including clicks, typing, scrolling, DOM states, screenshots, and HTTP requests for web agent training and evaluation.

## Overview

This dataset contains tasks performed by humans in real web environments, capturing:
- **Golden trajectories**: Step-by-step sequences of actions (clicks, typing, navigation)
- **Rich interaction data**: DOM states, screenshots, videos, and HTTP request captures
- **Evaluation checkpoints**: Intermediate validation points for multi-step tasks
- **Reproducible environments**: Bundled browser states for agent exploration

## Dataset Structure

Each task includes:
- `task_id`: Unique identifier
- `task_description`: Natural language description of the task
- `task_type`: Category of task (information retrieval, action-based, etc.)
- `trajectory`: Sequence of tool calls representing the golden trajectory
- `checkpoints`: Validation points for partial credit evaluation
- `reference_*`: URLs to supporting data (screenshots, videos, DOM snapshots, HTTP captures)

## Use Cases

- Training web agents with human demonstrations
- Evaluating agent performance with granular checkpoints
- Research on multi-hop and long-horizon web tasks
- Reinforcement learning with reproducible web environments

## Collection Methodology

Tasks were collected using a custom browser automation tool that captures:
- Every user interaction (clicks, typing, scrolling)
- DOM states at each step
- Screenshots and video recordings
- Intercepted HTTP/HTTPS requests
- Complete browser session data for environment reproduction

For detailed information about the collection tool and methodology, visit:
**[Building The Collection Tool](https://joan.so/learning/ml/research/browser-automation/1+Building+The+Collection+Tool)**

## Citation

If you use this dataset, please cite:

```bibtex
@misc{mind2web-subset-human,
  title={Mind2Web Subset - Human Demonstrations},
  author={Joan Cabezas},
  year={2025},
  url={https://joan.so/learning/ml/research/browser-automation/1+Building+The+Collection+Tool}
}
```

## License

MIT License - See repository for details.
