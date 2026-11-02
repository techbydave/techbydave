#!/usr/bin/env python3
"""
Tell search engines about new posts the moment they go live (IndexNow).

One ping reaches Bing, Yandex, Seznam, Naver and Yep; Bing also feeds
DuckDuckGo, Yahoo and ChatGPT search. Google doesn't use IndexNow, it finds
posts through your sitemaps and Search Console.

Run it AFTER the site has deployed. It:
  1. finds posts published in the last LOOKBACK_HOURS (their "published:" time,
     which the 9am publish step adds),
  2. waits until the site is serving the key file, so the engines can check it,
  3. sends those post URLs plus the homepage in one request.

If nothing new was published (like the 4am drafts run) it does nothing.
It never fails the workflow: problems are printed as warnings.

The key lives in build.py (INDEXNOW_KEY), which also publishes it as
/<key>.txt on the site.

Standard library only. Environment variables (all optional):
  SITE_URL         default "https://techbydave.com"
  LOOKBACK_HOURS   default 12
  DRY_RUN          "true" = print what would be sent, send nothing
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ENDPOINT = "https://api.indexnow.org/indexnow"
TIMEZONE = ZoneInfo("America/Toronto")
SITE_URL = (os.environ.get("SITE_URL", "").strip() or "https://techbydave.com").rstrip("/")
LOOKBACK_HOURS = float(os.environ.get("LOOKBACK_HOURS", "12") or 12)
DRY_RUN = os.environ.get("DRY_RUN", "").lower() == "true"
POSTS_DIR = Path("posts")
USER_AGENT = "techbydave-indexnow/1.0"


def warn(msg):
    print(f"WARNING: {msg}")


def read_key():
    m = re.search(r'^INDEXNOW_KEY\s*=\s*"([^"]*)"', Path("build.py").read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else ""


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower().strip()).strip("-")


def frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    meta = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
    return meta


def recent_post_urls():
    cutoff = datetime.now(TIMEZONE) - timedelta(hours=LOOKBACK_HOURS)
    urls = []
    for f in sorted(POSTS_DIR.glob("*.md")):
        meta = frontmatter(f.read_text(encoding="utf-8", errors="ignore"))
        raw = meta.get("published", "")
        if not raw or "title" not in meta:
            continue
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if not when.tzinfo:
            when = when.replace(tzinfo=TIMEZONE)
        if when >= cutoff:
            slug = meta.get("slug") or slugify(meta["title"])
            urls.append(f"{SITE_URL}/posts/{slug}.html")
    return urls


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "ignore")


def wait_until_live(urls, key, tries=10, pause=30):
    """Pages can take a minute or two to start serving a fresh deploy."""
    checks = [f"{SITE_URL}/{key}.txt"] + urls[:1]
    for attempt in range(1, tries + 1):
        try:
            ok = all(fetch(u)[0] == 200 for u in checks)
            if ok and fetch(checks[0])[1].strip() == key:
                return True
        except Exception:
            pass
        print(f"Waiting for the new post to be live ({attempt}/{tries})...")
        time.sleep(pause)
    return False


def main():
    if not POSTS_DIR.is_dir():
        warn("no posts/ folder here; run this from the repo root.")
        return
    key = read_key()
    if not key:
        warn("INDEXNOW_KEY is empty in build.py. Nothing sent.")
        return

    urls = recent_post_urls()
    if not urls:
        print(f"No posts published in the last {LOOKBACK_HOURS:g} hours. Nothing to send.")
        return
    urls.append(f"{SITE_URL}/")

    host = SITE_URL.split("//", 1)[-1].split("/", 1)[0]
    payload = {"host": host, "key": key, "keyLocation": f"{SITE_URL}/{key}.txt", "urlList": urls}

    if DRY_RUN:
        print("DRY RUN, would send:\n" + json.dumps(payload, indent=2))
        return

    if not wait_until_live(urls, key):
        warn("the site isn't serving the new post or key file yet. Sending anyway.")

    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow accepted {len(urls)} URL(s) (HTTP {r.status}):")
            for u in urls:
                print(f"  {u}")
    except urllib.error.HTTPError as e:
        hint = {
            400: "bad request (check the URLs).",
            403: "the key file wasn't found or doesn't match. Is the site live on this domain?",
            422: "these URLs don't belong to the site's domain, or the key doesn't match.",
            429: "too many requests; it'll work next time.",
        }.get(e.code, "")
        warn(f"IndexNow returned HTTP {e.code}. {hint}")
    except Exception as e:
        warn(f"couldn't reach IndexNow: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the deploy over this
        warn(f"IndexNow step failed: {e}")
    sys.exit(0)
