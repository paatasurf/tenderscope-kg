"""
Drive the batched permits import against a running tenderscope-kg deployment.

Usage
-----
    python scripts/run_permits_batches.py [--url URL] [--limit N] [--after-id N]

Repeatedly POSTs to /api/import/permits/batch, each call processing at most
--limit permit rows, until the server reports has_more=false. Each HTTP
request stays well under Railway's public-edge timeout since it only
processes one bounded slice per call.

Safe to re-run or resume: pass --after-id to continue from a specific
cursor (printed after every batch), and BCScraperPGImporter's upserts are
idempotent regardless, so re-processing an already-imported id range is
harmless.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://tenderscope-kg-production.up.railway.app"


def _post(url: str) -> dict:
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Base URL of the deployment")
    parser.add_argument("--limit", type=int, default=5000, help="Permit rows per batch")
    parser.add_argument("--after-id", type=int, default=0, help="Resume after this permits.id")
    args = parser.parse_args()

    after_id = args.after_id
    batch_num = 0
    total_created = 0
    total_updated = 0
    total_errors = 0

    while True:
        batch_num += 1
        endpoint = f"{args.url}/api/import/permits/batch?after_id={after_id}&limit={args.limit}"
        t0 = time.time()
        try:
            body = _post(endpoint)
        except urllib.error.HTTPError as exc:
            print(f"batch {batch_num}: HTTP {exc.code} — {exc.read().decode(errors='replace')}")
            return 1
        except urllib.error.URLError as exc:
            print(f"batch {batch_num}: request failed — {exc}")
            return 1
        elapsed = time.time() - t0

        permits = body["permits_batch"]
        total_created += permits["entities_created"]
        total_updated += permits["entities_updated"]
        total_errors += len(permits["errors"])

        print(
            f"batch {batch_num}: after_id={after_id} -> next_after_id={body['next_after_id']} "
            f"created={permits['entities_created']} updated={permits['entities_updated']} "
            f"errors={len(permits['errors'])} elapsed={elapsed:.1f}s has_more={body['has_more']}"
        )
        for err in permits["errors"]:
            print(f"    ERROR: {err}")

        if not body["has_more"]:
            break
        after_id = body["next_after_id"]

    print(
        f"\nDone. batches={batch_num} total_created={total_created} "
        f"total_updated={total_updated} total_errors={total_errors}"
    )
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
