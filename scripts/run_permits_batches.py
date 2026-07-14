"""
Drive a batched import endpoint against a running tenderscope-kg deployment.

Usage
-----
    python scripts/run_permits_batches.py [--kind {permits,contract_awards}] \\
        [--url URL] [--limit N] [--after-id N]

Repeatedly POSTs to /api/import/<kind>/batch, each call processing at most
--limit rows, until the server reports has_more=false. Each HTTP request
stays well under Railway's public-edge timeout since it only processes one
bounded slice per call. --kind defaults to "permits" (this script's original
scope); pass --kind contract_awards to drive that endpoint instead — both
batch endpoints share the same after_id/limit/has_more contract, so one
driver loop covers both.

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
VALID_KINDS = ("permits", "contract_awards")


def _post(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        choices=VALID_KINDS,
        default="permits",
        help="Which batch endpoint to drive (default: permits)",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Base URL of the deployment")
    parser.add_argument("--limit", type=int, default=5000, help="Rows per batch")
    parser.add_argument("--after-id", type=int, default=0, help="Resume after this row id")
    parser.add_argument(
        "--timeout",
        type=float,
        default=280.0,
        help="Client-side read timeout per batch, in seconds (keep under Railway's ~300s public-edge limit)",
    )
    args = parser.parse_args()

    result_key = f"{args.kind}_batch"
    after_id = args.after_id
    batch_num = 0
    total_created = 0
    total_updated = 0
    total_errors = 0

    while True:
        batch_num += 1
        endpoint = f"{args.url}/api/import/{args.kind}/batch?after_id={after_id}&limit={args.limit}"
        t0 = time.time()
        try:
            body = _post(endpoint, args.timeout)
        except urllib.error.HTTPError as exc:
            print(f"batch {batch_num}: HTTP {exc.code} — {exc.read().decode(errors='replace')}")
            return 1
        except urllib.error.URLError as exc:
            print(f"batch {batch_num}: request failed — {exc}")
            return 1
        except TimeoutError:
            print(
                f"batch {batch_num}: client timed out after {args.timeout}s waiting for "
                f"after_id={after_id} limit={args.limit}. The batch may still be running "
                f"server-side; re-run with --after-id {after_id} to retry, or lower --limit."
            )
            return 1
        elapsed = time.time() - t0

        stage = body[result_key]
        total_created += stage["entities_created"]
        total_updated += stage["entities_updated"]
        total_errors += len(stage["errors"])

        print(
            f"batch {batch_num}: after_id={after_id} -> next_after_id={body['next_after_id']} "
            f"created={stage['entities_created']} updated={stage['entities_updated']} "
            f"errors={len(stage['errors'])} elapsed={elapsed:.1f}s has_more={body['has_more']}"
        )
        for err in stage["errors"]:
            print(f"    ERROR: {err}")

        if not body["has_more"]:
            break
        after_id = body["next_after_id"]

    print(
        f"\nDone. kind={args.kind} batches={batch_num} total_created={total_created} "
        f"total_updated={total_updated} total_errors={total_errors}"
    )
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
