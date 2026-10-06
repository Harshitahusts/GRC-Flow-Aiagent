"""Download the approved external datasets listed in datasets/manifest.json.

Usage: python scripts/fetch_datasets.py [--all] [--only ID ...]

Files land in data/raw/<id>/ (git-ignored). Public Kaggle datasets download
without credentials; set KAGGLE_USERNAME/KAGGLE_KEY if a download returns 401/403.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "datasets" / "manifest.json"
RAW = ROOT / "data" / "raw"
KAGGLE_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download/{ref}"


def _request(ref: str) -> urllib.request.Request:
    req = urllib.request.Request(KAGGLE_DOWNLOAD.format(ref=ref))
    user, key = os.getenv("KAGGLE_USERNAME"), os.getenv("KAGGLE_KEY")
    if user and key:
        token = base64.b64encode(f"{user}:{key}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    return req


def fetch(entry: dict) -> None:
    dest = RAW / entry["id"]
    if dest.exists() and any(dest.iterdir()):
        print(f"skip   {entry['id']} (already present)")
        return
    with urllib.request.urlopen(_request(entry["kaggle_ref"]), timeout=300) as resp:
        payload = resp.read()
    dest.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(payload)).extractall(dest)
    print(f"fetched {entry['id']} ({len(payload) / 1e6:.1f} MB) <- {entry['kaggle_ref']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="include entries marked fetch=false")
    parser.add_argument("--only", nargs="*", help="dataset ids to fetch")
    args = parser.parse_args()

    entries = json.loads(MANIFEST.read_text())["datasets"]
    if args.only:
        entries = [e for e in entries if e["id"] in args.only]
    elif not args.all:
        entries = [e for e in entries if e.get("fetch")]

    failed = 0
    for entry in entries:
        try:
            fetch(entry)
        except Exception as exc:  # keep going; report at the end
            failed += 1
            print(f"FAILED {entry['id']}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
