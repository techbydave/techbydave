#!/usr/bin/env python3
"""
TechByDave: daily newsletter digest, sent through Kit.

Replaces the old Netlify Forms + Resend version. Kit now holds your
subscribers, sends the email, and adds the unsubscribe link.

Once a day (when the workflow runs, right after the daily post is written):
  1. finds posts in posts/ dated today (exits quietly if there are none),
  2. skips if today's digest was already created in Kit,
  3. creates a Kit broadcast to ALL your subscribers, scheduled to send
     15 minutes from now so the site has time to finish deploying.

The time of day is controlled by the cron lines in the workflow, not here.

Standard library only. Environment variables:
  KIT_API_KEY   Your Kit V4 API key (GitHub secret)   [required]
  SITE_URL      default "https://techbydave.com"
  DRY_RUN       "true" = check the key and print the email, send nothing
  FORCE         "true" = ignore the duplicate check
  DIGEST_DATE   override "today" for testing (YYYY-MM-DD)

The email uses images/email-header.png from your site, so keep that file in
your images/ folder.
"""

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://api.kit.com/v4"
TIMEZONE = "America/Toronto"
SEND_DELAY_MINUTES = 15
SITE_URL = (os.environ.get("SITE_URL", "").strip() or "https://techbydave.com").rstrip("/")
KIT_API_KEY = os.environ.get("KIT_API_KEY", "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "").lower() == "true"
FORCE = os.environ.get("FORCE", "").lower() == "true"
POSTS_DIR = Path("posts")

NOW_LOCAL = datetime.now(ZoneInfo(TIMEZONE))
TODAY = os.environ.get("DIGEST_DATE", "").strip() or NOW_LOCAL.strftime("%Y-%m-%d")


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def kit(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("X-Kit-Api-Key", KIT_API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")


def parse_frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip().lower()] = v.strip().strip('"').strip("'")
    return fm, m.group(2)


def find_todays_posts():
    posts = []
    if not POSTS_DIR.exists():
        fail(f"posts folder not found (cwd: {os.getcwd()})")
    for f in sorted(POSTS_DIR.glob("*.md")):
        fm, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        if fm.get("date", "")[:10] != TODAY:
            continue
        title = fm.get("title") or f.stem
        slug = fm.get("slug") or slugify(title)
        excerpt = fm.get("snippet") or fm.get("excerpt") or ""
        if not excerpt:
            for line in body.splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "!", "[[", "*As an Amazon")):
                    excerpt = line[:180] + ("…" if len(line) > 180 else "")
                    break
        posts.append({
            "title": title,
            "url": f"{SITE_URL}/posts/{slug}.html",
            "excerpt": excerpt,
            "image": post_image(fm, body),
            "tag": (fm.get("tags") or "").split(",")[0].strip(),
        })
    return posts


# ---------------------------------------------------------------------------
# The email. Same colours and type as the website (dark theme). Email apps
# ignore most modern CSS, so this is plain tables with inline styles.
# ---------------------------------------------------------------------------

SITE_NAME = "TechByDave"
HEADER_IMAGE = "email-header.png"          # lives in images/ on the site
SOCIALS = [
    ("Instagram", "https://www.instagram.com/techby_dave"),
    ("X", "https://x.com/techby_dave"),
    ("YouTube", "https://www.youtube.com/@techby_dave"),
    ("TikTok", "https://www.tiktok.com/@techby_dave"),
]

BG, PANEL, LINE = "#070a14", "#0e1324", "#1d2543"
INK, INK_SOFT, MUTE = "#e9edfa", "#c3cae3", "#8b95b6"
TRAIL, BEACON, ON_BEACON = "#7fd6ff", "#ffb347", "#1a1100"
# No quotes in these lists: they sit inside style='...' attributes.
DISPLAY = "Bricolage Grotesque,Segoe UI,Helvetica,Arial,sans-serif"
TEXT = "Newsreader,Georgia,Times New Roman,serif"


def esc(s):
    return html.escape(s or "", quote=True)


def image_url(path):
    """'photo.jpg', 'images/photo.jpg' or a full URL -> full URL on the site."""
    path = (path or "").strip().strip('"').strip("'")
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    for lead in ("./", "/", "images/"):
        while path.startswith(lead):
            path = path[len(lead):]
    return f"{SITE_URL}/images/{urllib.parse.quote(path)}"


def post_image(fm, body):
    if fm.get("image"):
        return image_url(fm["image"])
    m = re.search(r"!\[[^\]]*\]\(([^)\s]+)\)", body)
    return image_url(m.group(1)) if m else ""


def kicker(text):
    return (f"<div style='font:600 12px/1 {DISPLAY};letter-spacing:1.5px;text-transform:uppercase;"
            f"color:{TRAIL};margin:0 0 10px'>{esc(text)}</div>") if text else ""


def picture(p, radius=14):
    if not p["image"]:
        return ""
    return (f"<a href='{esc(p['url'])}' style='text-decoration:none'>"
            f"<img src='{esc(p['image'])}' alt='{esc(p['title'])}' width='536' "
            f"style='display:block;width:100%;max-width:536px;height:auto;border:1px solid {LINE};"
            f"border-radius:{radius}px;margin:0 0 18px'></a>")


def lead_story(p):
    return (
        f"<tr><td style='padding:28px 32px 8px'>"
        + picture(p)
        + kicker(p["tag"])
        + f"<a href='{esc(p['url'])}' style='text-decoration:none;color:{INK}'>"
          f"<h1 style='margin:0 0 12px;font:800 30px/1.1 {DISPLAY};letter-spacing:-0.5px;color:{INK}'>"
          f"{esc(p['title'])}</h1></a>"
        + (f"<p style='margin:0 0 22px;font:400 18px/1.55 {TEXT};color:{INK_SOFT}'>{esc(p['excerpt'])}</p>"
           if p["excerpt"] else "")
        + f"<table role='presentation' cellpadding='0' cellspacing='0' border='0'><tr>"
          f"<td style='background:{BEACON};border-radius:99px'>"
          f"<a href='{esc(p['url'])}' style='display:inline-block;padding:12px 22px;font:700 15px/1 {DISPLAY};"
          f"color:{ON_BEACON};text-decoration:none'>Read the story &rarr;</a></td></tr></table>"
        + "</td></tr>"
    )


def more_story(p):
    return (
        f"<tr><td style='padding:0 32px'><div style='border-top:1px solid {LINE};height:1px;line-height:1px'>&nbsp;</div></td></tr>"
        f"<tr><td style='padding:24px 32px 22px'>"
        + picture(p, 12)
        + kicker(p["tag"])
        + f"<a href='{esc(p['url'])}' style='text-decoration:none;color:{INK}'>"
          f"<h2 style='margin:0 0 8px;font:800 22px/1.2 {DISPLAY};letter-spacing:-0.3px;color:{INK}'>"
          f"{esc(p['title'])}</h2></a>"
        + (f"<p style='margin:0 0 10px;font:400 16px/1.55 {TEXT};color:{INK_SOFT}'>{esc(p['excerpt'])}</p>"
           if p["excerpt"] else "")
        + f"<a href='{esc(p['url'])}' style='font:600 14px/1 {DISPLAY};color:{TRAIL};text-decoration:none'>"
          f"Read more &rarr;</a></td></tr>"
    )


def build_html(posts):
    socials = " &nbsp;&middot;&nbsp; ".join(
        f"<a href='{esc(u)}' style='color:{MUTE};text-decoration:none'>{esc(n)}</a>" for n, u in SOCIALS)
    more = "".join(more_story(p) for p in posts[1:])
    return f"""<div style="margin:0;padding:24px 0;background:{BG}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BG}">
<tr><td align="center" style="padding:0 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
       style="width:100%;max-width:600px;background:{PANEL};border:1px solid {LINE};border-radius:18px;overflow:hidden">
  <tr><td style="padding:0">
    <a href="{esc(SITE_URL)}/" style="text-decoration:none">
      <img src="{esc(image_url(HEADER_IMAGE))}" alt="{SITE_NAME}: the daily digest" width="600"
           style="display:block;width:100%;max-width:600px;height:auto;border:0;background:{BG};color:{INK};font:800 28px {DISPLAY}">
    </a>
  </td></tr>
  {lead_story(posts[0])}
  <tr><td style="height:22px;line-height:22px">&nbsp;</td></tr>
  {more}
  <tr><td style="padding:28px 32px 0"><div style="border-top:1px solid {LINE};height:1px;line-height:1px">&nbsp;</div></td></tr>
  <tr><td style="padding:22px 32px 30px;font:500 14px/1.6 {DISPLAY};color:{MUTE}">
    <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:{BEACON};margin-right:8px"></span>
    <a href="{esc(SITE_URL)}/" style="color:{INK};text-decoration:none;font-weight:800">{SITE_NAME}</a>
    &nbsp;&middot;&nbsp; <a href="{esc(SITE_URL)}/" style="color:{TRAIL};text-decoration:none">{esc(SITE_URL.split('//')[-1])}</a><br>
    {socials}<br>
    <span style="font-size:13px">You're getting this because you subscribed at {esc(SITE_URL.split('//')[-1])}.</span>
  </td></tr>
</table>
</td></tr>
</table>
</div>"""


def already_created(subject):
    data = kit("/broadcasts?per_page=50")
    return any((b or {}).get("subject") == subject for b in data.get("broadcasts", []))


def main():
    if not KIT_API_KEY:
        fail("KIT_API_KEY is not set.")

    posts = find_todays_posts()
    if not posts:
        print(f"No posts dated {TODAY}. Nothing to send.")
        return

    subject = posts[0]["title"]
    preview = posts[0]["excerpt"][:140]
    send_at = (datetime.now(timezone.utc) + timedelta(minutes=SEND_DELAY_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")

    if DRY_RUN:
        acct = kit("/account")  # proves the API key works
        print(f"DRY RUN: Kit key OK ({json.dumps(acct)[:120]}).")
        print(f"Would send '{subject}' with {len(posts)} post(s) at {send_at}:")
        for p in posts:
            print(f"  - {p['title']}  {p['url']}")
        Path("digest-preview.html").write_text(build_html(posts), encoding="utf-8")
        print("Saved the email to digest-preview.html so you can open it in a browser.")
        return

    if not FORCE and already_created(subject):
        print("Today's digest was already created. Skipping.")
        return

    result = kit("/broadcasts", method="POST", body={
        "subject": subject,
        "preview_text": preview,
        "content": build_html(posts),
        "description": f"Daily digest {TODAY}",
        "public": False,
        "send_at": send_at,
    })
    bid = (result.get("broadcast") or {}).get("id")
    print(f"Created Kit broadcast {bid}: '{subject}', {len(posts)} post(s), scheduled for {send_at}.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"Digest failed: {e}", file=sys.stderr)
        sys.exit(1)
