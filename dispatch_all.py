#!/usr/bin/env python3
"""
Dispatch all CyberGym tasks to GitHub Actions workflows.
Downloads the task list from HuggingFace, then triggers workflow_dispatch
for each task via the GitHub API. Supports resume via dispatched.json.
"""

import json
import os
import sys
import time
import requests
from pathlib import Path

GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
DISPATCH_FILE = Path("dispatched.json")
WORKFLOW_ID = "run_task.yml"  # filename of the workflow

SESSION = requests.Session()
SESSION.headers["Accept"] = "application/vnd.github+json"
SESSION.headers["X-GitHub-Api-Version"] = "2022-11-28"
if GITHUB_TOKEN:
    SESSION.headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"


def get_workflow_id(repo: str) -> str:
    """Get the workflow ID (run_id) from the workflow filename."""
    url = f"https://api.github.com/repos/{repo}/actions/workflows"
    resp = SESSION.get(url)
    resp.raise_for_status()
    workflows = resp.json().get("workflows", [])
    for wf in workflows:
        if wf["path"] == f".github/workflows/{WORKFLOW_ID}":
            return wf["id"]
    # Fallback: return the filename as ID string (GitHub accepts either)
    return WORKFLOW_ID


def get_existing_dispatched() -> dict:
    if DISPATCH_FILE.exists():
        return json.loads(DISPATCH_FILE.read_text())
    return {}


def save_progress(dispatched: dict):
    DISPATCH_FILE.write_text(json.dumps(dispatched, indent=2))


def trigger_workflow(repo: str, workflow_id: str, task_id: str) -> bool:
    """Trigger a workflow_dispatch for a single task. Returns True on success."""
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_id}/dispatches"
    payload = {
        "ref": "main",
        "inputs": {"task_id": {"value": task_id}},
    }
    resp = SESSION.post(url, json=payload)
    if resp.status_code == 204:
        return True
    if resp.status_code == 429:
        # Rate limited — check Retry-After header
        retry_after = resp.headers.get("Retry-After", "60")
        print(f"  [RATE LIMITED] Sleeping {retry_after}s")
        time.sleep(int(retry_after))
        return trigger_workflow(repo, workflow_id, task_id)  # retry once
    print(f"  [ERROR {resp.status_code}] {resp.text}")
    return False


def fetch_task_ids() -> list[str]:
    try:
        from datasets import load_dataset
    except ImportError:
        print("ERROR: datasets library not installed. Run: pip install datasets")
        sys.exit(1)

    print("Fetching task list from HuggingFace (sunblaze-ucb/cybergym, split=test)...")
    ds = load_dataset("sunblaze-ucb/cybergym", split="test")
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
    if not GITHUB_REPO or not GITHUB_TOKEN:
        print("ERROR: GITHUB_REPO and GITHUB_TOKEN environment variables must be set")
        print("  export GITHUB_REPO=owner/repo")
        print("  export GITHUB_TOKEN=ghp_...")
        sys.exit(1)

    workflow_id = get_workflow_id(GITHUB_REPO)
    print(f"Workflow ID: {workflow_id}")

    all_tasks = fetch_task_ids()
    dispatched = get_existing_dispatched()

    already_done = set(dispatched.values()) if isinstance(dispatched, dict) else set()
    total = len(all_tasks)
    start = time.time()

    for i, task_id in enumerate(all_tasks):
        if task_id in already_done:
            print(f"[{i+1}/{total}] SKIP {task_id} (already dispatched)")
            continue

        print(f"[{i+1}/{total}] Dispatching {task_id}...", end=" ", flush=True)
        ok = trigger_workflow(GITHUB_REPO, workflow_id, task_id)
        if ok:
            dispatched[str(i)] = task_id
            save_progress(dispatched)
            print("OK")
        else:
            print(f"FAILED — stopping (will resume)")
            break

        # 1 second delay to avoid hitting rate limits
        time.sleep(1)

    elapsed = time.time() - start
    print(f"\nDone. Dispatched {len(dispatched)}/{total} tasks in {elapsed:.1f}s")
    print(f"Progress saved to {DISPATCH_FILE}")


if __name__ == "__main__":
    main()
