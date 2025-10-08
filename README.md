# Web Environments: Browser Agent Data Collection

> **Browser agents are hill climbing in the wrong direction.**

Existing browser agents aren't production-ready, and benchmarks either focus on academic tasks or lack real economic value. Most progress happens closed-source because data collection is prohibitively expensive.

## Goals

1. **Largest dataset:** Collect 10k+ browser interactions—2 orders of magnitude bigger than existing datasets
2. **Economic value:** Focus on long-horizon tasks tied to real work that people are paid to do
3. **Granular evaluation:** Identify real bottlenecks in existing agents with detailed checkpoints
4. **Open source recipe:** Develop an OSS approach for data collection and RL fine-tuning on any website

### Why This Tool?

Existing collection approaches are inadequate:
- **Mind2Web's tool** isn't open-sourced, requires extensive training, and doesn't create reproducible environments
- **WebArena/TheAgentCompany** spent hundreds of hours building website clones for just ~10 environments
- **Binary evaluations** provide sparse feedback without understanding failure modes

This tool collects everything needed for reproducible browser environments with zero training required after setup.

## Quick Start

**Requirements:** Python 3.11+

**Setup:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Run data collection:**
```bash
source .venv/bin/activate
RECORDER_BROWSER_CHANNEL=chrome python main.py
```

Provide task details when prompted; press Ctrl+C to end capture.

### What Gets Collected

Every browser interaction is captured for full reproducibility:
- **Human trajectories:** Screenshots, videos, DOMs, tool calls (click, type, navigation)
- **Network layer:** HAR files, API requests/responses, storage snapshots
- **Reproducible environments:** Complete browser state (cookies, localStorage, IndexedDB) to replay offline

Data structure:
- Captures: `data/<env>/captures/task_<id>/<timestamp>/`
- Task index: `data/<env>/tasks.db`
- Artifacts: `data/<env>/screenshots/`, `data/<env>/videos/`, `data/<env>/doms/`

## Usage

### Sandbox & Replay

**Launch a sandbox browser:**
```bash
python -m src.capture.sandbox --task-id <id> [--headed] [--allow-network-fallback]
```
Spins up a Playwright instance with the recorded environment. Use `--headed` for visible window, `--safe-mode` for minimal args if crashes occur.

**Replay a capture:**
```bash
python -m src.capture.replay <bundle_dir> [--headless] [--allow-network-fallback]
```
Replays the recorded environment offline; network fallback available if needed.

### Export & Process

**Export to JSONL:**
```bash
python src/tasks/db_to_jsonl_format.py [--prod]
```
Converts `tasks.db` into tool-call trajectories at `tasks.jsonl` with DOM snapshots.

**Generate checkpoints:**
```bash
export OPENAI_API_KEY=...
python src/tasks/extract_checkpoints.py
```
Uses LLM via `dspy` to create semantic checkpoints from human trajectories for granular evaluation.

### Evaluation

**Run Browser-Use agent:**
```bash
python src/eval/browseruse.py --model gpt-5-nano [--prod] [--sandbox-headed]
```
Evaluates agents in sandboxed environments. Outputs saved to `src/eval/results/`.

**Evaluate completions:**
```bash
python src/eval/evaluate.py <model_name> [judge_model] [--prod]
```
Compares agent trajectories against human baselines using checkpoint-based evaluation.

## Evaluation Approach

### Granular Checkpoints vs Binary Evals

Unlike existing benchmarks that only check final outcomes, this tool enables:
- **Semantic checkpoints:** LLM-generated intermediate goals from human trajectories
- **Partial rewards:** Credit for progress even on incomplete tasks
- **Failure analysis:** Identify where agents commonly fail

### Comparison to Existing Benchmarks

| Benchmark | Focus | Environment | Limitation |
|-----------|-------|-------------|------------|
| GAIA, BrowserComp | Deep research | Live web | Academic tasks, marginal economic value |
| Mind2Web | Information seeking | Snapshots | Mostly information retrieval |
| WebArena, WebVoyager | Execution | Clones (~10 sites) | 1+ years to build, not scalable |
| Real, TheAgentCompany | Action-based | Custom clones | Hundreds of hours per environment |

**This tool:** Automated collection of reproducible environments for any website at scale.

### Resources
- Benchmark analysis: https://web-evals.streamlit.app/
- Data collection tool: https://github.com/josancamon19/web-envs  
- Mind2Web subset: https://huggingface.co/datasets/josancamon/mind2web-subset-human

## GCP Data Upload

**Authenticate:**
```bash
gcloud auth application-default login
# Or use the desktop app: python desktop_app/task_collector_app.py
```

**Upload data:**
- Launch Task Collector app → "Upload Data"
- Uploads to `collection-reports` bucket with timestamp: `web-envs-data-YYYY-MM-DD_HH-MM-SS.zip`

## Configuration

**Environment variables:**
- `RECORDER_BROWSER_CHANNEL`: Browser to use (default: `chrome`)
- `RECORDER_USER_DATA_DIR`: Profile directory (default: `data/user-data`)
- `OPENAI_API_KEY`: Required for checkpoint generation and evaluation
- `KERNEL_API_KEY`: Optional, for non-sandboxed evaluation

**Notes:**
- Response bodies can be large; consider size limits/redaction for production
- Persistent Chrome profile reduces CAPTCHA/bot detection
