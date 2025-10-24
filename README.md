# Web Environments: Browser Agent Data Collection

> **Browser agents are hill climbing in the wrong direction.**

For understanding the ideas behind this repo, read: https://joan.so/learning/ml/research/browser-automation/0+main

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

**Requirements:** Python 3.13+, `uv` package manager

**Setup:**
```bash
# Install uv if you haven't
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

**Run data collection:**
```bash
uv run web-envs
```

Provide task details when prompted; press Ctrl+C to end capture.

### What Gets Collected

Every browser interaction is captured for full reproducibility:
- **Human trajectories:** Screenshots, videos, DOMs, tool calls (click, type, navigation)
- **Network layer:** HAR files, API requests/responses, storage snapshots
- **Reproducible environments:** Complete browser state (cookies, localStorage, IndexedDB) to replay offline

Data structure:
- Captures: `data/captures/task_<id>/<timestamp>/`
- Task index: `data/tasks.db`
- Artifacts: `data/screenshots/`, `data/videos/`, `data/doms/`

## Codebase Structure

```
web-environments/
├── src/
│   ├── main.py                 # Entry point for task recording
│   ├── browser/                # Stealth browser implementation
│   │   ├── browser.py          # Playwright browser wrapper
│   │   ├── recorder.py         # Event capture and recording logic
│   │   └── capture.py          # Network capture (HAR files)
│   ├── db/                     # Database management
│   │   ├── database.py         # SQLite wrapper
│   │   └── task.py             # Task CRUD operations
│   ├── environments/           # Replay and sandbox environments
│   │   ├── launch.py           # Launch recorded environment
│   │   ├── replay.py           # Replay captured steps
│   │   ├── capture.py          # Capture environment state
│   │   └── environment.py      # Sandbox environment wrapper
│   ├── scripts/
│   │   └── postprocessing/     # Data processing pipeline
│   │       ├── _1_tool_calls_format.py    # Convert events → tool calls
│   │       ├── _2_credentials.py          # Extract login credentials
│   │       └── _3_determine_checkpoints.py # LLM-based checkpoint extraction
│   ├── eval/                   # Agent evaluation framework
│   │   ├── run/
│   │   │   └── browseruse.py   # Browser-use agent runner
│   │   └── judges.py           # Checkpoint evaluation logic
│   ├── config/                 # Configuration files
│   └── utils/                  # Utility functions
└── data/
    ├── tasks.db                # SQLite database
    ├── tasks.jsonl             # Processed trajectories
    ├── captures/               # Recorded browser states
    ├── screenshots/            # Per-task screenshots
    ├── videos/                 # Per-task videos
    └── doms/                   # DOM snapshots
```

## CLI Entry Points

All commands are run with `uv run <command>` and support passing CLI parameters.

### 1. `web-envs` - Task Recording

**Purpose:** Launch a browser to record human task execution with full environment capture.

**When to use:** Starting a new data collection session for a task.

**Usage:**
```bash
uv run web-envs
```

**What it does:**
- Prompts for task details (description, type, website)
- Launches stealth browser with recording enabled
- Captures screenshots, video, DOM snapshots, network traffic
- Saves to `data/tasks.db` and creates capture bundle
- Press Ctrl+C when task is complete

**Outputs:**
- `data/captures/task_<id>/<timestamp>/` - Full environment bundle
- `data/screenshots/task<id>/` - Screenshots
- `data/videos/task<id>_*.webm` - Screen recording
- Database entry in `tasks.db`

---

### 2. Postprocessing Pipeline (Run in Order)

After recording tasks, run these scripts sequentially to process raw data into structured format.

#### `postprocess-toolcalls` - Convert Events to Tool Calls

**Purpose:** Parse raw browser events into structured tool call format.

**When to use:** After recording tasks, before credential extraction or checkpoints.

**Usage:**
```bash
uv run postprocess-toolcalls
```

**What it does:**
- Reads from `data/tasks.db`
- Processes events (clicks, types, navigations) into tool call format
- Enriches with DOM context, element attributes, timestamps
- Outputs to `data/tasks.jsonl`

**Output format:**
```json
{
  "task_id": 1,
  "task_description": "Book a flight from NYC to SF",
  "tool_calls": [
    {
      "type": "go_to",
      "url": "https://example.com",
      "timestamp": "2025-10-24T10:00:00.000Z"
    },
    {
      "type": "click",
      "selector": "#search-button",
      "element": {...},
      "timestamp": "2025-10-24T10:00:05.123Z"
    }
  ]
}
```

#### `postprocess-credentials` - Extract Login Credentials

**Purpose:** Identify and extract login credentials from task trajectories using LLM analysis.

**When to use:** After tool call formatting, when tasks involve authentication.

**Usage:**
```bash
# Requires OpenAI API key
export OPENAI_API_KEY=your_key
uv run postprocess-credentials
```

**What it does:**
- Uses DSPy with GPT-5 to analyze trajectories
- Identifies email, password, username, phone number fields
- Associates credentials with website domains
- Updates `data/tasks.jsonl` with credential metadata

**Why:** Enables proper handling of authentication during evaluation/replay.

#### `postprocess-set-checkpoints` - Generate Semantic Checkpoints

**Purpose:** LLM-based extraction of intermediate task checkpoints for granular evaluation.

**When to use:** After tool call formatting, when preparing for evaluation.

**Usage:**
```bash
# Requires OpenAI API key
export OPENAI_API_KEY=your_key
uv run postprocess-set-checkpoints
```

**What it does:**
- Uses DSPy with GPT-5 (medium reasoning) to analyze trajectories
- Identifies 2+ key steps that indicate task progress
- Generates reasoning for each checkpoint
- Updates `data/tasks.jsonl` with checkpoints

**Output:**
```json
{
  "checkpoints": [5, 12],
  "checkpoints_reasoning": [
    "User successfully logged into account",
    "Search results loaded with correct filters applied"
  ]
}
```

**Why:** Enables partial credit evaluation instead of binary pass/fail.

---

### 3. `launch-environment` - Replay Recorded Environment

**Purpose:** Launch a browser with a previously recorded environment for debugging or manual testing.

**When to use:** 
- Debugging recorded tasks
- Manually testing environment replay
- Verifying capture quality

**Usage:**
```bash
uv run launch-environment data/captures/task_10 [options]

# Options:
#   --headless                    Run without visible browser window
#   --no-allow-network-fallback   Fail if resources missing (strict offline)
#   --is-human-trajectory         Use human-like timing for replay
```

**Examples:**
```bash
# Launch with visible browser
uv run launch-environment data/captures/task_10

# Headless replay
uv run launch-environment data/captures/task_10 --headless

# Strict offline mode
uv run launch-environment data/captures/task_10 --no-allow-network-fallback
```

**What it does:**
- Loads recorded network traffic from HAR files
- Restores cookies, localStorage, IndexedDB
- Intercepts network requests and serves from capture
- Optionally replays human actions step-by-step

---

### 4. `evaluate-browseruse` - Run Agent Evaluation

**Purpose:** Evaluate browser-use agent performance on recorded tasks in sandboxed environments.

**When to use:** 
- Running benchmark evaluations
- Testing new agent versions
- Comparing different models

**Usage:**
```bash
uv run evaluate-browseruse [options]

# Options:
#   --model MODEL              LLM model name (default: gpt-5-nano)
#   --no-sandbox              Use live Kernel browser instead of replay
#   --sandbox-allow-network   Allow fallback to live network
#   --sandbox-headed          Show browser window
#   --sandbox-safe-mode       Headless + reduced args for stability
```

**Examples:**
```bash
# Default evaluation with GPT-5-nano
uv run evaluate-browseruse

# Use GPT-5 with visible browser
uv run evaluate-browseruse --model gpt-5 --sandbox-headed

# Live browser (no sandbox)
uv run evaluate-browseruse --model gpt-5-nano --no-sandbox

# Safe mode for stability
uv run evaluate-browseruse --sandbox-safe-mode
```

**What it does:**
- Loads tasks from `data/tasks.jsonl`
- Spins up sandboxed browser environments
- Runs browser-use agent on each task
- Saves results to `results/browseruse-{model}-{timestamp}/`
- Evaluates against human checkpoints

**Output:**
- Agent trajectories
- Checkpoint completion metrics
- Execution logs
- Failure analysis

---

## Complete Workflow

### 1. **Data Collection Phase**
```bash
# Record human demonstrations
uv run web-envs
# Repeat for multiple tasks
```

### 2. **Processing Phase** (Run in order)
```bash
# Step 1: Convert raw events to tool calls
uv run postprocess-toolcalls

# Step 2: Extract credentials (optional, for auth tasks)
export OPENAI_API_KEY=your_key
uv run postprocess-credentials

# Step 3: Generate evaluation checkpoints
uv run postprocess-set-checkpoints
```

### 3. **Validation Phase** (Optional)
```bash
# Verify environment replay works
uv run launch-environment data/captures/task_10 --headless
```

### 4. **Evaluation Phase**
```bash
# Run agent evaluation
uv run evaluate-browseruse --model gpt-5-nano
```

### 5. **Analysis**
Results are saved to timestamped directories with:
- Agent trajectories
- Checkpoint completion rates
- Execution logs
- Failure modes

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
