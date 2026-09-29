"""Score finished public gallery runs sequentially.

Usage: FIRST_READ_URL=https://... FIRST_READ_ADMIN_TOKEN=... python scripts/backfill_gallery_scores.py
"""

import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def _json(url: str, *, token: str | None = None, post: bool = False):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = Request(url, headers=headers, method="POST" if post else "GET")
    with urlopen(request, timeout=900 if post else 30) as response:
        return json.load(response)


def main() -> int:
    base = os.environ.get("FIRST_READ_URL", "").rstrip("/")
    token = os.environ.get("FIRST_READ_ADMIN_TOKEN", "")
    if not base.startswith("https://") or not token:
        print("Set HTTPS FIRST_READ_URL and FIRST_READ_ADMIN_TOKEN", file=sys.stderr)
        return 2
    runs = _json(f"{base}/api/runs?limit=200")
    scored = failed = skipped = 0
    for run in runs:
        if run["stage"] != "done" or (
            run.get("score_url") and not run.get("score_error")
        ):
            skipped += 1
            continue
        run_id = run["run_id"]
        try:
            _json(f"{base}/api/runs/{run_id}/score", token=token, post=True)
            for _ in range(180):
                current = _json(f"{base}/api/runs/{run_id}")
                if current["stage"] == "done":
                    break
                time.sleep(5)
            else:
                raise TimeoutError("score did not finish within 15 minutes")
            if current.get("score_url") and not current.get("score_error"):
                scored += 1
                print(f"scored {run_id}")
            else:
                failed += 1
                print(f"failed {run_id}: {current.get('score_error') or 'no score'}")
        except (HTTPError, TimeoutError, OSError) as error:
            failed += 1
            print(f"failed {run_id}: {error}")
    print(f"scored={scored} failed={failed} skipped={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
