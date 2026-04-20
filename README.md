# CyberGym GitHub Actions Runner

Runs the [CyberGym](https://huggingface.co/datasets/sunblaze-ucb/cybergym) benchmark (1,507 vulnerability reproduction tasks) entirely on free GitHub Actions runners.

Each task: an LLM agent receives a vulnerability description + unpatched C/C++ codebase and must produce a PoC input that crashes the binary. No 10 TB dataset download — every task has a pre-built public Docker image on Docker Hub (`n132/arvo:{task_id}-vul`).

## Architecture

```
GitHub Actions runner (free, 14 GB disk)
  └─ Pulls n132/arvo:{task_id}-vul from Docker Hub
       └─ /task/repo/      ← vulnerable C/C++ source
       └─ /task/description.txt  ← vulnerability description
       └─ /out/            ← compiled binary with sanitizers
  └─ Claude Code CLI runs headlessly inside container
       → produces /tmp/poc
  └─ result.json saved as workflow artifact
```

## Setup

### 1. Make the repository public

CyberGym runner images are on Docker Hub and don't require authentication.

### 2. Add `ANTHROPIC_API_KEY` as a repo secret

Go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (`sk-ant-...`) |

### 3. Install dependencies

```bash
pip install datasets requests
```

### 4. Set environment variables

```bash
export GITHUB_TOKEN=ghp_your_token_here
export GITHUB_REPO=owner/repo   # e.g. yourusername/cybergym-runner
```

Your `GITHUB_TOKEN` needs `repo` scope to trigger workflow dispatches.

## Running

### Dispatch all 1,507 tasks

```bash
python scripts/dispatch_all.py
```

This will:
- Download the full task list from HuggingFace
- Strip the `arvo:` prefix from each task ID
- Trigger `workflow_dispatch` for each task (1 second delay to avoid rate limits)
- Save progress to `dispatched.json` so it can resume if interrupted

### Monitor progress

Go to your repository's **Actions** tab. Each run corresponds to one task.

### Collect results

Once all (or a batch of) workflows have completed:

```bash
python scripts/collect_results.py
```

This will:
- List all completed workflow runs via the GitHub API
- Download and parse each `result.json` artifact
- Output `results_summary.json` with: total tasks run, successes, failures, success rate %

## Result format

Each `result.json` contains:

```json
{
  "task_id": "10400",
  "success": true,
  "binary_path": "/out/vulnerable_binary",
  "poc_hex": "4142434445..."
}
```

`success: true` means the binary crashed with the PoC (non-zero exit code or ASAN error detected).

## File overview

| File | Purpose |
|---|---|
| `.github/workflows/run_task.yml` | GitHub Actions workflow — one job per task |
| `run_agent.sh` | Runs inside each Docker container; executes Claude Code, verifies crash |
| `scripts/dispatch_all.py` | Downloads task list from HuggingFace, triggers all workflow dispatches |
| `scripts/collect_results.py` | Collects and aggregates result artifacts from completed runs |
| `README.md` | This file |

## Notes

- Each workflow run has a **35-minute timeout**.
- Claude Code runs with `--dangerously-skip-permissions --max-turns 30`.
- `dispatch_all.py` respects GitHub API rate limits by checking the `Retry-After` header.
- `dispatched.json` lets you safely re-run `dispatch_all.py` to fill in any missed tasks.
- Results are retained as artifacts for 30 days (GitHub Actions default).
