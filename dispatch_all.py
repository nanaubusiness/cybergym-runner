#!/usr/bin/env python3
"""
Dispatch all CyberGym tasks to GitHub Actions workflows.
Downloads the task list from HuggingFace, then triggers workflow_dispatch
for each task via the GitHub API (using `gh api` to avoid SSL issues).
Supports resume via dispatched.json.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

GITHUB_REPO = os.environ.get("GITHUB_REPO", "nanaubusiness/cybergym-runner")
DISPATCH_FILE = Path("dispatched.json")
WORKFLOW_FILE = "run_task.yml"


def gh_api(method: str, path: str, **kwargs) -> dict:
    """Call `gh api` with given method and path. Returns parsed JSON."""
    cmd = ["gh", "api", "--header", "X-GitHub-Api-Version:2022-11-28", path]
    if method != "GET":
        cmd.insert(2, "--method")
        cmd.insert(3, method)

    # Handle request body for POST
    body = kwargs.get("body")
    if body:
        body_file = kwargs.get("body_file")
        if body_file:
            cmd.extend(["--input", body_file])
        elif isinstance(body, dict):
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump(body, f)
                f.flush()
                cmd.extend(["--input", f.name])
                body_file_path = f.name
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                os.unlink(body_file_path)
            except Exception:
                os.unlink(body_file_path)
                raise
            if result.returncode != 0:
                raise RuntimeError(f"gh api failed: {result.stderr}")
            return json.loads(result.stdout) if result.stdout.strip() else {}
        else:
            cmd.extend(["--input", body])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"gh api {method} {path} failed: {result.stderr}")
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        return {}


def get_workflow_id(repo: str) -> int:
    """Get the workflow numeric ID from the workflow filename."""
    data = gh_api("GET", f"/repos/{repo}/actions/workflows")
    for wf in data.get("workflows", []):
        if wf["path"] == f".github/workflows/{WORKFLOW_FILE}":
            return wf["id"]
    raise RuntimeError(f"Workflow {WORKFLOW_FILE} not found in {repo}")


def trigger_workflow(repo: str, workflow_id: int, task_id: str) -> bool:
    """Trigger workflow_dispatch for a single task via gh api. Returns True on success."""
    # workflow_dispatch inputs: simple key->value map (strings only)
    payload = {
        "ref": "main",
        "inputs": {"task_id": task_id},
    }
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        f.flush()
        tmp = f.name

    try:
        result = subprocess.run(
            ["gh", "api", "--method", "POST",
             f"/repos/{repo}/actions/workflows/{workflow_id}/dispatches",
             "--header", "X-GitHub-Api-Version:2022-11-28",
             "--input", tmp],
            capture_output=True, text=True, timeout=120
        )
        os.unlink(tmp)
    except Exception as e:
        os.unlink(tmp)
        raise

    if result.returncode == 204 or result.returncode == 0:
        return True
    stderr = result.stderr.lower()
    if "rate limit" in stderr or result.returncode == 429:
        # Extract retry-after if present
        retry_match = result.stderr
        print(f"  [RATE LIMITED] Will retry after delay")
        time.sleep(60)
        # Retry once
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            f.flush()
            tmp = f.name
        try:
            result = subprocess.run(
                ["gh", "api", "--method", "POST",
                 f"/repos/{repo}/actions/workflows/{workflow_id}/dispatches",
                 "--header", "X-GitHub-Api-Version:2022-11-28",
                 "--input", tmp],
                capture_output=True, text=True, timeout=120
            )
            os.unlink(tmp)
        except Exception:
            os.unlink(tmp)
            raise
        return result.returncode in (0, 204)
    print(f"  [ERROR {result.returncode}] {result.stderr[:200]}")
    return False


def get_existing_dispatched() -> dict:
    if DISPATCH_FILE.exists():
        return json.loads(DISPATCH_FILE.read_text())
    return {}


def save_progress(dispatched: dict):
    DISPATCH_FILE.write_text(json.dumps(dispatched, indent=2))


def fetch_task_ids() -> list[str]:
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: datasets library not installed. Run: pip install datasets")
        sys.exit(1)

    print("Fetching task list from HuggingFace (sunblaze-ucb/cybergym, split=tasks)...")
    ds = load_dataset("sunblaze-ucb/cybergym", split="tasks")
    ids = []
    for row in ds:
        task_id = row.get("task_id", "")
        if not task_id:
            continue
        # Task IDs look like "arvo:10400" — strip the "arvo:" prefix
        if ":" in task_id:
            task_id = task_id.split(":", 1)[1]
        ids.append(task_id.strip())
    print(f"Found {len(ids)} tasks")
    return ids


def main():
    repo = GITHUB_REPO
    if not repo:
        print("ERROR: GITHUB_REPO environment variable must be set")
        sys.exit(1)

    print(f"Repo: {repo}")
    workflow_id = get_workflow_id(repo)
    print(f"Workflow ID: {workflow_id}")

    all_tasks = fetch_task_ids()
    dispatched = get_existing_dispatched()

    # dispatched is dict: str(index) -> task_id string
    # Build set of already-dispatched task_ids
    already_done = set()
    if isinstance(dispatched, dict):
        for idx, tid in dispatched.items():
            already_done.add(tid)
    # Also skip ones already in-flight by checking recent workflow runs
    total = len(all_tasks)
    start = time.time()
    dispatched_count = 0

    for i, task_id in enumerate(all_tasks):
        if task_id in already_done:
            print(f"[{i+1}/{total}] SKIP {task_id} (already dispatched)")
            continue

        print(f"[{i+1}/{total}] Dispatching {task_id}...", end=" ", flush=True)
        ok = trigger_workflow(repo, workflow_id, task_id)
        if ok:
            dispatched[str(i)] = task_id
            save_progress(dispatched)
            already_done.add(task_id)
            dispatched_count += 1
            print("OK")
        else:
            print(f"FAILED — stopping (will resume on next run)")
            break

        # 1 second delay to avoid rate limits
        time.sleep(1)

    elapsed = time.time() - start
    print(f"\nDone. Dispatched {dispatched_count}/{total} tasks in {elapsed:.1f}s")
    print(f"Progress saved to {DISPATCH_FILE}")


if __name__ == "__main__":
    main()
