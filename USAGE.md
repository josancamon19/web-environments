# Usage Instructions

## Running the Application

The application supports two modes: **dev** (development) and **prod** (production).

### Default Mode
- **Default mode**: `dev`
- **Default data directory**: `data/dev/`

### Command Line Options

#### Development Mode (default)
```bash
python src/eval/parse_tasks.py
# or explicitly
python src/eval/parse_tasks.py --dev
# or
python src/eval/parse_tasks.py --mode=dev
```

#### Production Mode
```bash
python src/eval/parse_tasks.py --prod
# or
python src/eval/parse_tasks.py --mode=prod
```

### Environment Variable
You can also set the mode using an environment variable:
```bash
export APP_MODE=prod
python src/eval/parse_tasks.py
```

### Data Directory Structure

#### Development Mode
```
data/
└── dev/
    ├── tasks.db
    ├── tasks.jsonl
    ├── screenshots/
    └── videos/
```

#### Production Mode
```
data/
└── prod/
    ├── tasks.db
    ├── tasks.jsonl
    ├── screenshots/
    └── videos/
```

### Examples

1. **Run in development mode** (default):
   ```bash
   python src/eval/parse_tasks.py
   ```

2. **Run in production mode**:
   ```bash
   python src/eval/parse_tasks.py --prod
   ```

3. **Specify custom paths** (overrides mode-based paths):
   ```bash
   python -c "from src.eval.parse_tasks import parse; parse('custom/path/tasks.db', 'custom/output.jsonl')"
   ```

The application will automatically create the necessary directories if they don't exist.
