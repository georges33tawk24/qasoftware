"""Drive one real run through a running control plane and report what came back.

Deliberately a thin client over the HTTP API: it proves the same path a user takes,
rather than reaching into the engine from the host.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(api: str, path: str, body: Any = None, method: str = "GET") -> Any:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{api.rstrip('/')}{path}",
        data=data,
        headers={"content-type": "application/json"},
        method=method,
    )
    try:
        with OPENER.open(request, timeout=120) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} -> {exc.code} {exc.read().decode()[:400]}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--name", default="")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--viewports", default="desktop_1440")
    parser.add_argument("--flows", action="store_true")
    parser.add_argument("--probes", action="store_true")
    parser.add_argument("--project", default="", help="reuse an existing project id")
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    presets = {
        "mobile_390": {"name": "mobile_390", "width": 390, "height": 844, "deviceScaleFactor": 2.0},
        "tablet_834": {
            "name": "tablet_834",
            "width": 834,
            "height": 1112,
            "deviceScaleFactor": 2.0,
        },
        "desktop_1440": {"name": "desktop_1440", "width": 1440, "height": 900},
    }
    config = {
        "viewports": [presets[v] for v in args.viewports.split(",") if v in presets],
        "maxPages": args.max_pages,
        "maxDepth": args.max_depth,
        "flows": args.flows,
        "apiProbes": args.probes,
    }

    if args.project:
        project = call(args.api, f"/api/projects/{args.project}")
    else:
        project = call(
            args.api,
            "/api/projects",
            {"name": args.name or args.target, "target": args.target, "config": config},
            "POST",
        )
        print(f"project {project['id']}  {project['target']}")

    started = time.time()
    run = call(args.api, f"/api/projects/{project['id']}/runs", {"triggeredBy": "realrun"}, "POST")
    print(f"run {run['id']} queued")

    deadline = started + args.timeout
    state = ""
    while time.time() < deadline:
        run = call(args.api, f"/api/runs/{run['id']}")
        if run["state"] != state:
            state = run["state"]
            print(f"  [{time.time() - started:6.1f}s] {state}")
        if state in ("complete", "failed", "aborted"):
            break
        time.sleep(5)
    else:
        print("timed out waiting for the run", file=sys.stderr)
        return 2

    elapsed = time.time() - started
    print(f"\n=== {state} in {elapsed:.0f}s ===")
    print(f"pages   {run['pages']}")
    print(f"issues  {run['issues']}")
    print(f"counts  {run['counts']}")
    if run.get("error"):
        print(f"error   {run['error']}")

    issues = call(args.api, f"/api/projects/{project['id']}/issues")
    by_category: Counter[str] = Counter()
    by_checker: Counter[str] = Counter()
    for issue in issues:
        by_category[issue["category"]] += 1
        by_checker[issue["checkerId"]] += 1

    print("\n--- by category ---")
    for name, count in by_category.most_common():
        print(f"  {name:14} {count}")
    print("\n--- by checker ---")
    for name, count in by_checker.most_common():
        print(f"  {name:32} {count}")

    if args.out:
        payload = {
            "project": project,
            "run": run,
            "elapsedSeconds": round(elapsed, 1),
            "issues": issues,
        }
        Path(args.out).write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
