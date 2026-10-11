#!/usr/bin/env python3
"""Fetch Cyprus legislation sources into a local cache (_raw/cy/cache).

Default run (what the published corpus was built from):
  * Greek consolidated Constitution from CyLaw (one index page + one full-text page)
  * English translation of the Constitution (rev. 2013) from the Constitute Project

Optional, DISABLED unless --cylaw-written-consent is passed:
  * every basic law listed in CyLaw's consolidated-legislation indices
    (Constitution, colonial Caps, laws 1959..current year).

CyLaw's terms of use (https://www.cylaw.org/terms.html) prohibit "mass or systematic
downloading of files or documents by any means, including automated processing" without the
prior written consent of KINOP (Cyprus Institute of Legal Information). Only run the --all-laws
mode if such consent has been obtained (info@cylaw.org).

Usage:
  python cy_fetch.py                      # constitution (el + en)
  python cy_fetch.py --all-laws --cylaw-written-consent   # full corpus (needs consent!)
"""
import argparse
import datetime
import os
import re
import sys
import time
from pathlib import Path

import requests

UA = "LawCodex/1.0 (+https://github.com/LawCodex)"
CYLAW = "https://www.cylaw.org"
CONSTITUTE = "https://www.constituteproject.org/service/html?cons_id=Cyprus_2013&lang=en"
ROOT = Path(__file__).resolve().parents[2]
RAW = Path(os.environ.get("CY_RAW", ROOT / "_raw" / "cy" / "cache"))
DELAY = 1.0  # seconds between requests (robots.txt has no Crawl-delay; be polite anyway)

session = requests.Session()
session.headers["User-Agent"] = UA
_last = [0.0]


def get(url, dest: Path, refresh=False):
    if dest.exists() and dest.stat().st_size > 0 and not refresh:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        wait = DELAY - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = session.get(url, timeout=60)
        except requests.RequestException as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
            time.sleep(5 * 2 ** attempt)
            continue
        if r.status_code == 404:
            print(f"  404 {url}", file=sys.stderr)
            return None
        if r.status_code != 200:
            print(f"  ! {r.status_code} {url}", file=sys.stderr)
            time.sleep(5 * 2 ** attempt)
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(r.content)
        tmp.rename(dest)
        return dest
    raise RuntimeError(f"failed: {url}")


def cylaw_path(url_path):
    """Map a CyLaw URL path to a cache file."""
    return RAW / "cylaw" / url_path.lstrip("/")


def fetch_law(index_path, refresh=False):
    """Fetch a law's CyLaw index page and its consolidated full text."""
    ip = get(CYLAW + index_path, cylaw_path(index_path), refresh)
    if not ip:
        return
    html = ip.read_bytes().decode("windows-1253", "replace")
    m = re.search(r'href="(/nomoi/enop/[^"]+/full\.html)"', html)
    if m:
        get(CYLAW + m.group(1), cylaw_path(m.group(1)), refresh)
    else:
        print(f"  no consolidated text for {index_path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-laws", action="store_true")
    ap.add_argument("--cylaw-written-consent", action="store_true",
                    help="confirm KINOP/CyLaw granted written consent for bulk download")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    print("Constitution (CyLaw, Greek)")
    fetch_law("/nomoi/indexes/syntagma.html", args.refresh)
    print("Constitution (Constitute Project, English rev. 2013)")
    get(CONSTITUTE, RAW / "constitute" / "Cyprus_2013_en.json", args.refresh)

    if not args.all_laws:
        return
    if not args.cylaw_written_consent:
        sys.exit("Refusing: CyLaw terms of use prohibit bulk/systematic downloading without "
                 "KINOP's prior written consent. Pass --cylaw-written-consent once obtained.")
    years = ["0"] + [str(y) for y in range(1959, datetime.date.today().year + 1)]
    for y in years:
        p = f"/nomoi/{y}_index.html"
        ip = get(CYLAW + p, cylaw_path(p), refresh=True)
        if not ip:
            continue
        html = ip.read_bytes().decode("windows-1253", "replace")
        links = sorted(set(re.findall(r'href="(/nomoi/indexes/[^"]+\.html)"', html)))
        print(f"{y}: {len(links)} laws")
        for link in links:
            fetch_law(link, args.refresh)


if __name__ == "__main__":
    main()
