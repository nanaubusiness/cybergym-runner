#!/usr/bin/env python3
"""
Collect CyberGym results from completed GitHub Actions workflow runs.
Lists all workflow runs for the dispatch workflow, downloads result artifacts,
parses each result.json, and prints a summary.
"""

import json
import os
import sys
import time
import zipfile
import io
import requests
from pathlib import Path
from datetime import datetime

GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
WORKFLOW_NAME = "Run CyberGym Task"
OUTPUT_FILE = Path("results_summary.json")

SESSION = requests.Session()
SESSION.headers["Accept"] = "application/vnd.github+json"
SESSION.headers["X-GitHub-Api-Version"] = "2022-11-28"
if GITHUB_TOKEN:
    SESSION.headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"


def get_workflow_id(repo: str) -> str | None:
    url = f"https://api.github.com/repos/{repo}/actions/workflows"
    resp = SESSION.get(url)
    resp.raise_for_status()
    for wf in resp.json().get("workflows", []):
        if wf["name"] == WORKFLOW_NAME:
            return str(wf["id"])
    return None


def list_workflow_runs(repo: str, workflow_id: str, max_pages: int = 10) -> list[dict]:
    runs = []
    page = 1
    per_page = 100
    while page <= max_pages:
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_id}/runs"
        params = {"per_page": per_page, "page": page}
        resp = SESSION.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        runs.extend(data.get("workflow_runs", []))
        if not data.get("workflow_runs"):
            break
        page += 1
        time.sleep(0.5)
    return runs


def download_artifact(repo: str, artifact_id: int, artifact_name: str) -> dict | None:
    url = f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip"
    resp = SESSION.get(url, allow_redirects=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for name in zf.namelist():
            if name.endswith(".json"):
                with zf.open(name) as f:
                    return json.load(f)
    return None


def main():
    if not GITHUB_REPO or not GITHUB_TOKEN:
        print("ERROR: GITHUB_REPO and GITHUB_TOKEN environment variables must be set")
        print("  export GITHUB_REPO=owner/repo")
        print("  export GITHUB_TOKEN=ghp_...")
        sys.exit(1)

    print(f"Fetching workflow runs for {GITHUB_REPO}...")
    workflow_id = get_workflow_id(GITHUB_REPO)
    if not workflow_id:
        print(f"ERROR: Could not find workflow '{WORKFLOW_NAME}'")
        sys.exit(1)
    print(f"Workflow ID: {workflow_id}")

    runs = list_workflow_runs(GITHUB_REPO, workflow_id)
    print(f"Found {len(runs)} total workflow runs")

    # Collect only completed runs
    results = []
    completed = 0
    skipped = 0

    for run in runs:
        status = run.get("status", "")
        conclusion = run.get("conclusion", "")
        run_id = run["id"]
        name = run.get("name", "")
        # Extract task_id from workflow run name (format: "Run CyberGym Task 10400")
        task_id = name.replace("Run CyberGym Task", "").strip()

        if status != "completed":
            skipped += 1
            continue
        completed += 1

        # Get artifacts for this run
        artifacts_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}/artifacts"
        artifacts_resp = SESSION.get(artifacts_url)
        artifacts = artifacts_resp.json().get("artifacts", []) if artifacts_resp.ok else []

        result_entry = {
            "run_id": run_id,
            "task_id": task_id,
            "conclusion": conclusion,
            "result": None,
        }

        for artifact in artifacts:
            if "result" in artifact.get("name", "").lower():
                parsed = download_artifact(GITHUB_REPO, artifact["id"], artifact["name"])
                if parsed:
                    result_entry["result"] = parsed
                    break

        results.append(result_entry)

    # Summarise
    total = len(results)
    successes = sum(1 for r in results if r.get("result", {}).get("success") == True)
    failures = total - successes

    summary = {
        "collected_at": datetime.utcnow().isoformat() + "Z",
        "total_runs": len(runs),
        "completed_runs": completed,
        "skipped_runs": skipped,
        "results_collected": total,
        "successes": successes,
        "failures": failures,
        "success_rate": round(successes / total * 100, 2) if total > 0 else 0.0,
        "results": results,
    }

    OUTPUT_FILE.write_text(json.dumps(summary, indent=2))

    print(f"\n{'='*50}")
    print(f"  Total workflow runs : {len(runs)}")
    print(f"  Completed runs      : {completed}")
    print(f"  Skipped (in-progress): {skipped}")
    print(f"  Results collected   : {total}")
    print(f"  {'='*50}")
    print(f"  SUCCESSES           : {successes}")
    print(f"  FAILURES            : {failures}")
    print(f"  SUCCESS RATE        : {summary['success_rate']}%")
    print(f"{'='*50}")
    print(f"\nFull summary written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
