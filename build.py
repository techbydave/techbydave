#!/usr/bin/env python3
"""
TechByDave static site builder.

Reads Markdown posts from posts/*.md and generates a static site into dist/.
Pure Python standard library — no dependencies to install, ever.

Usage:
    python3 build.py

Then either:
  - drag the dist/ folder onto https://app.netlify.com/drop, or
  - push this whole project to GitHub and point Netlify at it with
    build command `python3 build.py` and publish directory `dist`.

Homepage thumbnails
-------------------
Each post can set a cover image in its frontmatter:

    ---
    title: My post
    date: 2026-10-28
    tags: Amazon, Echo
    image: echo-show-16.jpg        # file inside images/  (or a full https:// URL)
                                   # "images/echo-show-16.jpg" works too
    ---

If `image:` is missing, the first image inside the post body is used.
If the post has no image at all, a themed placeholder is shown instead.
"""
import re
import os
import shutil
import html
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SITE_TITLE = "TechByDave"
SITE_TAGLINE = "Worth it or a waste? Honest gadget verdicts and smarter buys."
SITE_URL = "https://techbydave.com/"  # replace with your real domain

# Cloudflare Web Analytics — free, no cookie banner needed, one script
# tag. Leave blank to skip analytics entirely.
CLOUDFLARE_ANALYTICS_TOKEN = os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN") or "140dbf46d09a4b9987daee1d0e0c0b39"  # public Web Analytics beacon token

# Kit (ConvertKit) form that newsletter signups are sent to. Both the popup
# and the bottom-of-page signup post here.
KIT_FORM_ID = "10041123"

# IndexNow: lets the site tell Bing (and Yandex, Seznam, Naver...) the moment a
# post goes live. The key is public on purpose: it's published as /<key>.txt so
# search engines can check the ping really came from this site.
# scripts/indexnow.py reads it from here, so change it in this one place only.
INDEXNOW_KEY = "7257c729ecdade3df71a251e41eb32f7"
KIT_FORM_ACTION = f"https://app.kit.com/forms/{KIT_FORM_ID}/subscriptions"

# Who runs the site and how readers reach you.
AUTHOR_NAME = "Dave"
AUTHOR_EMAIL = "Davis@techbydave.com"
DEFAULT_SHARE_IMAGE = "share-default.jpg"  # link-preview picture (in images/) for pages without their own photo
AMAZON_STOREFRONT = "https://www.amazon.com/shop/influencer-d02827d8"  # Dave's Amazon Influencer page
ABOUT_PHOTO = "dave-portrait.jpg"   # your photo on the About page, in images/
INFO_EMAIL = "info@techbydave.com"

# Publish times. Posts get an exact "published:" time when the 9am publish step
# moves them live. Older posts without one are treated as published at this
# hour (Toronto time) on their date.
TIMEZONE = ZoneInfo("America/Toronto")
DEFAULT_PUBLISH_HOUR = 9
NEWS_SITEMAP_HOURS = 48  # Google News sitemaps should only list the last 2 days

# One line under the big homepage headline.
HERO_SUBLINE = "Worth it or a waste? Honest verdicts on new gadgets, and smarter ways to spend on tech."

# Homepage layout: 1 big lead story, then SIDE_COUNT stories in the 2x2 grid
# beside it, then MORE_COUNT stories in the "More stories" grid. Anything
# older falls into a simple list at the bottom.
SIDE_COUNT = 4
MORE_COUNT = 9


def _analytics_snippet():
    if not CLOUDFLARE_ANALYTICS_TOKEN:
        return ""
    return (
        '<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
        f'data-cf-beacon=\'{{"token": "{CLOUDFLARE_ANALYTICS_TOKEN}"}}\'></script>'
    )


# Each is (label, url). Shows up in the footer on every page.
# Leave the list empty ( [] ) to show no social links at all.
SOCIAL_LINKS = [
    ("Instagram", "https://www.instagram.com/techby_dave"),
    ("x", "https://x.com/techby_dave"),
    ("Youtube", "https://www.youtube.com/@techby_dave"),
    ("tiktok", "https://www.tiktok.com/@techby_dave"),
    ("Pinterest", "https://ca.pinterest.com/techby_dave/"),
]

ROOT = os.path.dirname(os.path.abspath(__file__))
POSTS_DIR = os.path.join(ROOT, "posts")
IMAGES_DIR = os.path.join(ROOT, "images")
SOCIAL_CARDS_DIR = os.path.join(ROOT, "social-cards")
FAVICON_DIR = os.path.join(ROOT, "favicon")  # favicon files, copied to the top of the site
DIST_DIR = os.path.join(ROOT, "dist")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(text, filename):
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', text, re.DOTALL)
    if not m:
        raise ValueError(
            f"{filename}: missing '---' frontmatter block at the top of the file"
        )
    meta_block, body = m.groups()
    meta = {}
    for line in meta_block.splitlines():
        if not line.strip() or ':' not in line:
            continue
        key, _, value = line.partition(':')
        meta[key.strip()] = value.strip()

    for required in ("title", "date"):
        if required not in meta:
            raise ValueError(f"{filename}: frontmatter is missing '{required}:'")

    try:
        meta["_date_obj"] = datetime.strptime(meta["date"], "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"{filename}: date '{meta['date']}' isn't in YYYY-MM-DD format"
        )

    meta.setdefault("tags", "")
    meta.setdefault("snippet", "")
    meta["_published"] = parse_published(meta)
    return meta, body


def parse_published(meta):
    """Exact publish time as a timezone-aware datetime."""
    raw = meta.get("published", "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=TIMEZONE)
        except ValueError:
            pass
    return meta["_date_obj"].replace(hour=DEFAULT_PUBLISH_HOUR, tzinfo=TIMEZONE)


# ---------------------------------------------------------------------------
# Minimal Markdown -> HTML (headers, bold, italic, code, links, lists, quotes)
# ---------------------------------------------------------------------------

IMG_MARK = "__IMG__/"          # stands for "the images/ folder"; swapped per page
MISSING_IMAGES = set()        # filled during the build, reported at the end


def image_name(path):
    """Accepts 'photo.jpg', 'images/photo.jpg', '/images/photo.jpg' or './images/photo.jpg'."""
    p = path.strip().strip('"').strip("'")
    while True:
        for lead in ("./", "/", "images/"):
            if p.startswith(lead):
                p = p[len(lead):]
                break
        else:
            return p


def resolve_image_src(path):
    path = path.strip().strip('"').strip("'")
    if path.startswith("http://") or path.startswith("https://"):
        return path
    name = image_name(path)
    if not os.path.isfile(os.path.join(IMAGES_DIR, name)):
        MISSING_IMAGES.add(name)
    return IMG_MARK + urllib.parse.quote(name)  # spaces and brackets in file names are safe


def fix_images(text, prefix):
    """Turn the images/ marker into a working path: "" on root pages, "../" on post pages,
    or the full site URL for the RSS feed and Apple News."""
    return text.replace(IMG_MARK, prefix + "images/")


def inline_md(text):
    text = html.escape(text, quote=False)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', text)
    # Images must run BEFORE the link regex.
    text = re.sub(
        r'!\[([^\]]*)\]\(([^)]+)\)',
        lambda m: f'<img src="{resolve_image_src(m.group(2))}" alt="{m.group(1)}">',
        text,
    )
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    return text


def _strip_md(t):
    return re.sub(r'[*_`]|\[([^\]]*)\]\([^)]*\)', lambda m: m.group(1) or "", t)


BLOCK_START = re.compile(r'^(#{2,3})\s+|^[-*]\s+|^>|^```')


def markdown_to_html(md):
    lines = md.replace('\r\n', '\n').split('\n')
    out = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        if line.strip().startswith('```'):
            lang = line.strip()[3:].strip()
            code_lines = []
            i += 1
            while i < n and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            code = html.escape('\n'.join(code_lines))
            cls = f' class="language-{lang}"' if lang else ''
            out.append(f'<pre><code{cls}>{code}</code></pre>')
            continue

        m = re.match(r'^(#{2,3})\s+(.*)$', line)
        if m:
            level = len(m.group(1))
            out.append(f'<h{level} id="{slugify(_strip_md(m.group(2)))}">{inline_md(m.group(2))}</h{level}>')
            i += 1
            continue

        # A line that is only bold text ("**Who should care**") is a section heading.
        bm = re.match(r'^\*\*([^*]+?)\*\*:?\s*$', line.strip())
        if bm and (i + 1 >= n or not lines[i + 1].strip()):
            out.append(f'<h2 id="{slugify(bm.group(1))}">{inline_md(bm.group(1))}</h2>')
            i += 1
            continue

        if line.strip().startswith('>'):
            quote_lines = []
            while i < n and lines[i].strip().startswith('>'):
                quote_lines.append(lines[i].strip().lstrip('>').strip())
                i += 1
            out.append(f'<blockquote>{inline_md(" ".join(quote_lines))}</blockquote>')
            continue

        if re.match(r'^[-*]\s+', line.strip()):
            items = []
            while i < n and re.match(r'^[-*]\s+', lines[i].strip()):
                item_text = re.sub(r'^[-*]\s+', '', lines[i].strip())
                items.append(f'<li>{inline_md(item_text)}</li>')
                i += 1
            out.append('<ul>' + ''.join(items) + '</ul>')
            continue

        if not line.strip():
            i += 1
            continue

        para_lines = [line]
        i += 1
        while i < n and lines[i].strip() and not BLOCK_START.match(lines[i].strip()):
            para_lines.append(lines[i])
            i += 1
        out.append(f'<p>{inline_md(" ".join(l.strip() for l in para_lines))}</p>')

    return '\n'.join(out)


def slugify(s):
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return s.strip('-')


def meta_description(text, body_md="", limit=155):
    """A search-friendly description: at least ~70 characters, at most ~155.
    Short or missing snippets are topped up from the post's first paragraph."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) < 70 and body_md:
        for para in re.split(r"\n\s*\n", body_md):
            para = para.strip()
            if not para or para.startswith(("#", "!", ">", "-", "*", "[[", "|")):
                continue
            plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", para)
            plain = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", plain)
            plain = re.sub(r"[*_`]", "", plain)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain and plain != text:
                text = f"{text} {plain}".strip() if text else plain
                break
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-— ")
        text = cut + "…"
    return text


def fmt_date_human(d):
    return f"{d.strftime('%b')} {d.day}, {d.year}"


# ---------------------------------------------------------------------------
# Sitemap, robots.txt, RSS
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"^[ \t]*\[\[ADD YOUR [^\n]*\]\][ \t]*\n?", re.M)
_EMPTY_SECTION_RE = re.compile(r"^#{2,3}[ \t]*My take[^\n]*\n\s*(?=^#{1,3}\s|^\*|\Z)", re.M)


def strip_placeholders(body):
    """Remove "[[ADD YOUR TAKE ...]]" / "[[ADD YOUR PHOTO ...]]" reminder lines,
    plus a "My take" heading left with nothing under it, so readers never see them."""
    body = _PLACEHOLDER_RE.sub("", body)
    return _EMPTY_SECTION_RE.sub("", body)


_MY_TAKE_RE = re.compile(r"^#{2,3}\s*My take\b", re.I | re.M)


def in_google(meta):
    """Should Google show this post in search?

    Posts with a "## My take" section (Dave's own view) are shown. Posts without
    one stay on the site for readers but are hidden from Google with a "noindex"
    tag and left out of the sitemaps, so thin auto-written posts don't drag down
    how Google rates the whole site. Adding a take later brings a post back
    automatically.

    To override for one post, add a line to its frontmatter:
        google: yes   -> always shown (e.g. a post already getting search traffic)
        google: no    -> always hidden
    """
    choice = meta.get("google", "").strip().lower()
    if choice in ("yes", "true", "show", "index"):
        return True
    if choice in ("no", "false", "hide", "noindex"):
        return False
    return bool(_MY_TAKE_RE.search(meta.get("_body_md", "")))


def generate_sitemap(posts):
    posts = [m for m in posts if in_google(m)]
    base = SITE_URL.rstrip("/")
    urls = [f"{base}/", f"{base}/about.html", f"{base}/editorial-policy.html",
            f"{base}/contact.html", f"{base}/privacy.html", f"{base}/media-kit.html"]
    urls += [f"{base}/archive.html"]
    urls += [f"{base}/topics/{slug}.html" for _, slug, _ in TOPICS
             if any(slug in {t for _, t in topics_for(m)} for m in posts)]
    urls += [f"{base}/posts/{m['slug']}.html" for m in posts]
    entries = "".join(f"\n  <url><loc>{html.escape(u)}</loc></url>" for u in urls)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}
</urlset>"""


def generate_news_sitemap(posts):
    """Google News sitemap: only articles published in the last 48 hours."""
    posts = [m for m in posts if in_google(m)]
    base = SITE_URL.rstrip("/")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=NEWS_SITEMAP_HOURS)
    recent = [m for m in posts if m["_published"] >= cutoff]
    if not recent and posts:
        # Nothing new in 2 days: list the newest post so the file is never empty.
        # (An empty news sitemap shows up in Search Console as "Missing XML tag".)
        recent = [max(posts, key=lambda m: m["_published"])]
    entries = []
    for m in recent:
        entries.append(f"""
  <url>
    <loc>{html.escape(f"{base}/posts/{m['slug']}.html")}</loc>
    <news:news>
      <news:publication><news:name>{html.escape(SITE_TITLE)}</news:name><news:language>en</news:language></news:publication>
      <news:publication_date>{m["_published"].isoformat(timespec="seconds")}</news:publication_date>
      <news:title>{html.escape(m["title"])}</news:title>
    </news:news>
  </url>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">{''.join(entries)}
</urlset>"""


def generate_robots_txt():
    base = SITE_URL.rstrip("/")
    return f"""User-agent: *
Allow: /

Sitemap: {base}/sitemap.xml
Sitemap: {base}/news-sitemap.xml
"""


def generate_rss(posts):
    base = SITE_URL.rstrip("/")
    items = []
    for meta in posts[:20]:  # most recent 20 items
        link = f"{base}/posts/{meta['slug']}.html"
        pub_date = meta["_published"].strftime("%a, %d %b %Y %H:%M:%S %z")
        # Cover image as a full URL, so Pinterest, Flipboard and feed readers can show it.
        image = post_image(meta)
        media = ""
        if image:
            full = html.escape(fix_images(image, base + "/"))
            ext = full.rsplit(".", 1)[-1].lower()
            mime = {"png": "image/png", "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
            media = (f'\n      <enclosure url="{full}" length="0" type="{mime}"/>'
                     f'\n      <media:content url="{full}" medium="image" type="{mime}"/>')
        summary = meta_description(meta["snippet"], meta.get("_body_md", ""), limit=480)
        items.append(f"""
    <item>
      <title>{html.escape(meta['title'])}</title>
      <link>{link}</link>
      <guid>{link}</guid>
      <pubDate>{pub_date}</pubDate>{media}
      <media:description>{html.escape(summary)}</media:description>
      <description><![CDATA[{fix_images(meta['body_html'], base + '/')}]]></description>
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>{html.escape(SITE_TITLE)}</title>
    <link>{base}/</link>
    <atom:link href="{base}/feed.xml" rel="self" type="application/rss+xml"/>
    <description>{html.escape(SITE_TAGLINE)}</description>
    <language>en-us</language>
{''.join(items)}
  </channel>
</rss>"""


# ---------------------------------------------------------------------------
# Apple News Format (ANF) article generation
# ---------------------------------------------------------------------------

def _strip_tags(s):
    return re.sub(r'<[^>]+>', '', s).strip()


_IMG_ONLY_RE = re.compile(r'^<p>\s*<img src="([^"]+)" alt="[^"]*">\s*</p>$')


def _html_block_to_component(block):
    block = block.strip()
    if block.startswith('<h2>'):
        return {"role": "heading2", "text": _strip_tags(block)}
    if block.startswith('<h3>'):
        return {"role": "heading3", "text": _strip_tags(block)}
    if block.startswith('<blockquote>'):
        return {"role": "quote", "text": _strip_tags(block)}
    # A paragraph that's ONLY an image becomes a dedicated ANF image component.
    m = _IMG_ONLY_RE.match(block)
    if m:
        src = m.group(1)
        url = src if src.startswith("http") else fix_images(src, SITE_URL.rstrip('/') + "/")
        return {"role": "image", "URL": url}
    return {"role": "body", "text": block, "format": "html"}


def generate_anf(meta):
    components = [{"role": "title", "text": meta["title"]}]
    if meta.get("snippet"):
        components.append({"role": "intro", "text": meta["snippet"]})

    for block in meta["body_html"].split("\n"):
        if block.strip():
            components.append(_html_block_to_component(block))

    return {
        "version": "1.7",
        "identifier": meta["slug"],
        "language": "en",
        "title": meta["title"],
        "layout": {"columns": 12, "width": 1024, "margin": 60, "gutter": 20},
        "components": components,
        "metadata": {
            "canonicalURL": f"{SITE_URL.rstrip('/')}/posts/{meta['slug']}.html",
            "datePublished": meta["_published"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generatorIdentifier": "TechByDaveBuildPy",
            "generatorName": "TechByDave build.py",
            "generatorVersion": "1.0",
        },
    }


# ---------------------------------------------------------------------------
# Design: dark-first theme with animated terrain hero
# ---------------------------------------------------------------------------

CSS = """
:root{
  --bg:#070a14; --panel:#0e1324; --line:#1d2543; --ink:#e9edfa; --ink-soft:#c3cae3; --mute:#8b95b6;
  --trail:#7fd6ff; --beacon:#ffb347; --on-beacon:#1a1100; --code:#131a30; color-scheme:dark;
  --display:"Bricolage Grotesque",system-ui,sans-serif; --text:"Newsreader",Georgia,serif;
}
html[data-theme="light"]{
  --bg:#f3f5fb; --panel:#ffffff; --line:#d6dcf0; --ink:#12172b; --ink-soft:#343c5c; --mute:#566087;
  --trail:#1b6fd1; --beacon:#d9780b; --on-beacon:#ffffff; --code:#e9edf8; color-scheme:light;
}
*{box-sizing:border-box;margin:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--ink);font:400 1.125rem/1.55 var(--text);-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
img{max-width:100%;display:block}
:focus-visible{outline:2px solid var(--beacon);outline-offset:3px;border-radius:4px}
::view-transition-old(root),::view-transition-new(root){animation:none;mix-blend-mode:normal}
.wrap{max-width:1100px;margin:0 auto;padding:0 1.5rem}

.progress{position:fixed;left:0;right:0;top:0;height:3px;z-index:60;pointer-events:none}
.progress i{position:absolute;inset:0;background:linear-gradient(90deg,transparent,var(--trail));transform-origin:left;transform:scaleX(var(--p,0));will-change:transform}
.progress b{position:absolute;top:-3px;left:0;transform:translateX(calc(var(--p,0)*100vw));will-change:transform;width:9px;height:9px;margin-left:-4px;border-radius:50%;background:var(--beacon);box-shadow:0 0 14px 3px var(--beacon)}

header.site-head{position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--bg) 95%,transparent);border-bottom:1px solid var(--line)}
.bar{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding-top:1rem;padding-bottom:1rem}
.brand{font:800 1.35rem/1 var(--display);letter-spacing:-.02em;display:flex;align-items:center;gap:.6rem}
.brand .dot{width:10px;height:10px;border-radius:50%;background:var(--beacon);position:relative}
.brand .dot::after{content:"";position:absolute;inset:-6px;border-radius:50%;border:1.5px solid var(--beacon);animation:ping 2.4s ease-out infinite}
@keyframes ping{0%{transform:scale(.4);opacity:.9}100%{transform:scale(1.5);opacity:0}}
nav.site-nav{display:flex;align-items:center;gap:1.4rem;font:500 .95rem var(--display)}
nav.site-nav a{color:var(--mute);transition:color .2s}
nav.site-nav a:hover{color:var(--ink)}
#theme-toggle{border:1px solid var(--line);background:var(--panel);color:var(--ink);font:500 .85rem var(--display);padding:.45rem .85rem;border-radius:99px;cursor:pointer;display:flex;gap:.45rem;align-items:center}
#theme-toggle svg{width:15px;height:15px;transition:transform .5s cubic-bezier(.3,1.6,.5,1)}
#theme-toggle:hover svg{transform:rotate(90deg)}

/* ---- About page photo card ---- */
.about-card{display:flex;align-items:center;gap:22px;margin:8px 0 28px;padding:20px;background:var(--panel);
  border:1px solid var(--line);border-radius:18px}
.about-photo{width:120px;height:120px;border-radius:50%;object-fit:cover;flex:none;
  border:3px solid var(--beacon);box-shadow:0 0 0 6px rgba(255,179,71,.12)}
.about-name{font-weight:800;font-size:1.4rem;color:var(--ink)}
.about-role{color:var(--mute);margin-top:4px}
@media (max-width:480px){.about-card{flex-direction:column;text-align:center}}

/* ---- Header groups, burger button, social icons ---- */
.bar-left,.bar-right{display:flex;align-items:center}
.bar-left{gap:1.1rem;min-width:0}
.bar-right{gap:.7rem}
.bar-left nav.site-nav{margin-left:.9rem}
.soc{display:flex;align-items:center;gap:.2rem}
.soc a{width:34px;height:34px;display:grid;place-items:center;border-radius:50%;color:var(--mute);font:600 .85rem var(--display);transition:color .2s,background .2s}
.soc a:hover{color:var(--ink);background:var(--panel)}
.soc svg{width:18px;height:18px}
.nl-btn{display:inline-flex;gap:.5rem;align-items:center;font:500 .85rem var(--display);padding:.45rem .85rem;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:99px;cursor:pointer;transition:border-color .2s,color .2s}
.nl-btn:hover{border-color:var(--trail);color:var(--trail)}
.nl-btn svg{width:15px;height:15px}
@media (max-width:1000px){.soc-head{display:none}}
@media (max-width:760px){.bar-left nav.site-nav{display:none}}
@media (max-width:560px){.nl-btn .lbl{display:none}.nl-btn{padding:.5rem}}

/* Burger: three bars that spin and fold into an X */
.burger{position:relative;flex:none;width:42px;height:42px;border:0;border-radius:12px;background:none;color:var(--ink);cursor:pointer;transition:transform .6s cubic-bezier(.3,1.5,.5,1),color .3s,background .2s}
.burger:hover{background:var(--panel)}
.burger span{position:absolute;left:10px;width:22px;height:2px;border-radius:2px;background:currentColor;transition:transform .5s cubic-bezier(.3,1.4,.5,1),width .3s,opacity .25s}
.burger span:nth-child(1){top:14px}
.burger span:nth-child(2){top:20px;width:15px}
.burger span:nth-child(3){top:26px}
.burger:hover span:nth-child(2){width:22px}
.burger[aria-expanded="true"]{transform:rotate(180deg);color:var(--beacon)}
.burger[aria-expanded="true"] span:nth-child(1){transform:translateY(6px) rotate(45deg)}
.burger[aria-expanded="true"] span:nth-child(2){transform:scaleX(0);opacity:0}
.burger[aria-expanded="true"] span:nth-child(3){transform:translateY(-6px) rotate(-45deg)}

/* Slide-out menu */
.drawer-scrim{position:fixed;inset:0;z-index:44;background:rgba(4,6,14,.6);opacity:0;pointer-events:none;transition:opacity .35s}
.drawer-scrim.open{opacity:1;pointer-events:auto}
.drawer{position:fixed;left:0;top:var(--head-h,62px);bottom:0;width:min(340px,88vw);z-index:45;display:flex;flex-direction:column;padding:1.2rem 1.6rem 2rem;overflow-y:auto;background:var(--panel);border-right:1px solid var(--line);transform:translateX(-102%);visibility:hidden;transition:transform .5s cubic-bezier(.2,.85,.2,1),visibility 0s .5s}
.drawer.open{transform:none;visibility:visible;transition:transform .5s cubic-bezier(.2,.85,.2,1),visibility 0s}
.drawer .item{opacity:0;transform:translateX(-14px);transition:opacity .2s,transform .2s}
.drawer.open .item{opacity:1;transform:none;transition:opacity .4s calc(var(--i)*45ms + 160ms),transform .55s cubic-bezier(.2,.85,.2,1) calc(var(--i)*45ms + 160ms)}
.drawer-nav{display:flex;flex-direction:column}
.drawer-nav a{font:800 1.65rem/1 var(--display);letter-spacing:-.03em;padding:.8rem 0;color:var(--ink)}
.drawer-nav a:hover{color:var(--trail)}
.drawer-mid{display:flex;flex-wrap:wrap;align-items:center;gap:.8rem 1rem;margin:1.4rem 0;padding:1.4rem 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.drawer-small{display:flex;flex-direction:column;gap:.8rem;margin-top:auto;font:500 .9rem var(--display);color:var(--mute)}
.drawer-small a:hover{color:var(--ink)}

.hero{position:relative;overflow:hidden;border-bottom:1px solid var(--line)}
.hero canvas{position:absolute;inset:0;width:100%;height:100%;opacity:.5}
.hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(color-mix(in srgb,var(--bg) 45%,transparent),transparent 30%,transparent 70%,var(--bg))}
.hero .in{position:relative;z-index:2;padding-top:2.5rem;padding-bottom:3.5rem}
.hero h1{font:800 clamp(2.8rem,9vw,6.6rem)/.95 var(--display);letter-spacing:-.045em;max-width:9ch}
.hero h1 .w{display:inline-block;overflow:hidden;vertical-align:top;padding-bottom:.12em;margin-right:.22em}
.hero h1 .c{display:inline-block;transform:translateY(112%);animation:rise .95s cubic-bezier(.2,.85,.2,1) forwards;animation-delay:calc(var(--i)*42ms + 250ms)}
@keyframes rise{to{transform:none}}
.hero p.sub{margin-top:1.6rem;max-width:30ch;color:var(--mute);font-size:1.25rem;opacity:0;animation:fade .9s 1.4s forwards}
@keyframes fade{to{opacity:1}}
@keyframes lift{to{opacity:1;transform:none}}

main{padding:2.5rem 0 1rem}
main.home{padding-top:2.5rem}

/* ---- Homepage: lead story + 2x2 grid, then "More stories" ---- */
.top{display:grid;grid-template-columns:1.12fr 1fr;gap:2rem 2.4rem;align-items:start;opacity:0;transform:translateY(26px);animation:lift .9s .2s cubic-bezier(.2,.85,.2,1) forwards}
.top.solo{grid-template-columns:1fr}
.top-side{display:grid;grid-template-columns:1fr 1fr;gap:1.8rem 1.4rem;align-content:start}

.thumb{position:relative;display:block;overflow:hidden;border-radius:14px;border:1px solid var(--line);aspect-ratio:16/10;background:var(--panel)}
.thumb img{width:100%;height:100%;object-fit:cover;margin:0;transition:transform .6s cubic-bezier(.2,.85,.2,1)}
.story:hover .thumb img,.story:focus-visible .thumb img{transform:scale(1.04)}
.thumb.ph{background:
  radial-gradient(circle at 78% 30%,color-mix(in srgb,var(--beacon) 55%,transparent) 0 5px,transparent 6px),
  radial-gradient(120% 90% at 70% 110%,color-mix(in srgb,var(--trail) 32%,transparent),transparent 62%),
  linear-gradient(160deg,var(--panel),var(--bg))}
.pill{position:absolute;left:.7rem;top:.7rem;z-index:2;font:600 .78rem/1 var(--display);padding:.42rem .75rem;border-radius:99px;background:color-mix(in srgb,var(--bg) 90%,transparent);border:1px solid var(--line);color:var(--ink)}

.story{display:block}
.story h2,.story h3{font-family:var(--display);font-weight:800;letter-spacing:-.025em;transition:color .25s}
.story:hover h2,.story:hover h3,.story:focus-visible h2,.story:focus-visible h3{color:var(--trail)}
.byline{display:flex;gap:.9rem;align-items:center;font:500 .85rem var(--display);color:var(--mute);margin-top:.6rem}
.byline .live{display:inline-flex;align-items:center;gap:.4rem;color:var(--beacon)}
.byline .live::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor;animation:blink 1.6s infinite}
@keyframes blink{50%{opacity:.25}}

.story-lead h2{font-size:clamp(1.8rem,3.6vw,2.9rem);line-height:1.06;margin-top:1.3rem;max-width:20ch}
.story-lead p.dek{margin-top:.9rem;color:var(--ink-soft);font-size:1.12rem;max-width:52ch}
.story-card h3{font-size:clamp(1.05rem,1.7vw,1.3rem);line-height:1.18;margin-top:.9rem}

.hero-tag{font:600 .95rem/1.4 var(--display);color:var(--ink-soft);margin:0 0 1.2rem;letter-spacing:.01em}.hero-tag::before{content:"";display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--beacon);margin-right:.6em;vertical-align:.1em}
.sec-title{font:800 1.7rem/1 var(--display);letter-spacing:-.03em;text-align:center;margin:4rem 0 1.8rem}
.more{display:grid;grid-template-columns:repeat(3,1fr);gap:2.2rem 1.6rem}
.more .story-card h3{font-size:1.25rem}
.more .story-card p.dek{margin-top:.5rem;color:var(--mute);font-size:1rem;max-width:44ch;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}

.older{padding-top:.5rem}

@media (max-width:900px){
  .top{grid-template-columns:1fr}
  .more{grid-template-columns:repeat(2,1fr)}
}
@media (max-width:560px){
  .top-side{grid-template-columns:1fr}
  .more{grid-template-columns:1fr}
}

.feed{list-style:none;padding:0}
.feed a{position:relative;display:grid;grid-template-columns:7.5rem 1fr;gap:.4rem 1.5rem;padding:1.5rem 0 1.5rem 1.3rem;border-top:1px solid var(--line)}
.feed li:last-child a{border-bottom:1px solid var(--line)}
.feed a::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:linear-gradient(var(--trail),var(--beacon));transform:scaleY(0);transform-origin:top;transition:transform .45s cubic-bezier(.2,.85,.2,1)}
.feed a:hover::before,.feed a:focus-visible::before{transform:scaleY(1)}
.feed time{font:500 .85rem var(--display);color:var(--mute);padding-top:.5rem}
.feed time em{display:block;font-style:normal;color:var(--trail);margin-top:.2rem}
.feed h3{font:800 clamp(1.25rem,2.4vw,1.7rem)/1.15 var(--display);letter-spacing:-.02em;transition:transform .35s cubic-bezier(.2,.85,.2,1)}
.feed a:hover h3{transform:translateX(10px)}
.feed p{grid-column:2;color:var(--mute);max-width:60ch;font-size:1.02rem;max-height:0;opacity:0;overflow:hidden;transition:max-height .45s ease,opacity .35s,transform .35s;transform:translateX(10px)}
.feed a:hover p,.feed a:focus-visible p{max-height:6rem;opacity:1}
@media (hover:none){.feed p{max-height:none;opacity:1;transform:none}}
@media (max-width:640px){.feed a{grid-template-columns:1fr}.feed p{grid-column:1}}

.post-full{padding:1rem 0 3rem;max-width:760px;min-width:0}
.post-layout{display:grid;grid-template-columns:minmax(0,760px);justify-content:center;gap:3.5rem}
.post-side{display:none}
@media (min-width:1100px){.post-layout{grid-template-columns:minmax(0,740px) 260px}.post-side{display:block}
  .side-sticky{position:sticky;top:96px;display:flex;flex-direction:column;gap:1rem;padding-top:7.5rem}.toc-inline{display:none}}
.post-body h2,.post-body h3{scroll-margin-top:90px}
.read-time{white-space:nowrap}
.hands-on{display:inline-flex;align-items:center;gap:.3rem;margin-right:.6rem;padding:.18rem .6rem;border-radius:999px;font-size:.78rem;font-weight:700;background:color-mix(in srgb,var(--beacon) 16%,transparent);color:var(--beacon);border:1px solid color-mix(in srgb,var(--beacon) 40%,transparent)}
.hands-on-pill{background:var(--beacon)!important;color:#111!important}
.verdict{margin:-.6rem 0 1.8rem;padding:1.1rem 1.25rem 1.2rem;border-radius:16px;background:var(--panel);border:1px solid var(--line);border-left:5px solid var(--vc,var(--beacon))}
.v-buy{--vc:#2fbf71}.v-wait{--vc:var(--beacon)}.v-skip{--vc:#ef5a5a}
.v-head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.6rem}
.v-label{font:700 .78rem var(--display);letter-spacing:.12em;text-transform:uppercase;color:var(--mute)}
.v-pills{display:flex;gap:.35rem}
.vpill{font:800 .8rem var(--display);letter-spacing:.06em;padding:.3rem .7rem;border-radius:99px;border:1px solid var(--line);color:var(--mute)}
.vpill.on{background:var(--vc);border-color:var(--vc);color:#0b0f1a}
.v-line{margin:.75rem 0 0;font:600 1.12rem/1.45 var(--display);color:var(--ink)}
.price-btn{display:inline-flex;flex-direction:column;margin-top:1rem;padding:.7rem 1.1rem;border-radius:12px;background:var(--beacon);color:var(--on-beacon);font:700 .98rem var(--display);transition:transform .15s,filter .2s}
.price-btn:hover{filter:brightness(1.08);transform:translateY(-1px)}
.price-for{font:500 .75rem var(--display);opacity:.75;max-width:30ch;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.price-btn.small{margin-top:.8rem;padding:.55rem .85rem;font-size:.88rem;width:100%}
.price-card{margin:.5rem 0 0}
.side-verdict{padding:1rem 1.1rem;border-radius:14px;background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--vc)}
.side-verdict b{display:block;font:800 1.9rem/1 var(--display);color:var(--vc);margin-top:.35rem;letter-spacing:-.02em}
.toc{font-family:var(--display)}
.toc ol{list-style:none;margin:0;padding:0;counter-reset:t}
.toc li{counter-increment:t}
.toc a{display:flex;gap:.6rem;padding:.42rem 0;color:var(--ink-soft);font-size:.92rem;line-height:1.3;transition:color .2s}
.toc a::before{content:counter(t,decimal-leading-zero);color:var(--mute);font-size:.75rem;padding-top:.15rem}
.toc a:hover,.toc a.on{color:var(--trail)}
.toc-side{padding:1rem 1.1rem;border-radius:14px;border:1px solid var(--line)}
.toc-title{font:700 .78rem var(--display);letter-spacing:.12em;text-transform:uppercase;color:var(--mute);margin-bottom:.4rem}
.toc-inline{margin:0 0 1.8rem;border:1px solid var(--line);border-radius:14px;padding:.85rem 1.1rem;background:var(--panel)}
.toc-inline summary{cursor:pointer;font:700 .95rem var(--display);color:var(--ink)}
.toc-inline ol{margin-top:.5rem}
.keep-reading{margin-top:3rem;padding-top:2rem;border-top:1px solid var(--line)}
.keep-reading h2{font:800 1.6rem/1.1 var(--display);letter-spacing:-.025em;margin-bottom:1.2rem}
.kr-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1.2rem}
.kr-grid .story-card h3{font-size:1.05rem}
.kr-grid .byline{margin-top:.45rem}
@media (max-width:700px){.kr-grid{grid-template-columns:1fr}.kr-grid .story-card{display:grid;grid-template-columns:110px 1fr;gap:.9rem;align-items:start}.kr-grid .story-card h3{margin-top:0}.kr-grid .thumb{grid-row:span 2}.kr-grid .byline{grid-column:2}}
.to-top{position:fixed;right:1rem;bottom:1.2rem;z-index:40;width:46px;height:46px;border-radius:50%;display:grid;place-items:center;background:var(--panel);border:1px solid var(--line);color:var(--ink);font:700 1.2rem var(--display);opacity:0;pointer-events:none;transform:translateY(10px);transition:opacity .25s,transform .25s;box-shadow:0 8px 24px rgba(0,0,0,.35)}
.to-top.show{opacity:1;pointer-events:auto;transform:none}
@media (min-width:1100px){.to-top{display:none}}
.skip-link{position:absolute;left:1rem;top:-60px;z-index:200;background:var(--beacon);color:var(--on-beacon);font:700 .95rem var(--display);padding:.7rem 1rem;border-radius:10px;transition:top .2s}
.skip-link:focus{top:1rem}
main:focus{outline:none}
a:focus-visible,button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--trail);outline-offset:3px;border-radius:6px}
.vtag{position:absolute;right:.7rem;top:.7rem;z-index:2;font:800 .72rem/1 var(--display);letter-spacing:.08em;padding:.42rem .65rem;border-radius:99px;background:var(--vc);color:#0b0f1a}
.tags-label{font:600 .8rem var(--display);color:var(--mute);align-self:center;margin-right:.2rem}
.author-box{display:flex;gap:1.1rem;align-items:flex-start;margin-top:2rem;padding:1.3rem 1.4rem;border:1px solid var(--line);border-radius:18px;background:var(--panel)}
.ab-photo{width:72px;height:72px;border-radius:50%;object-fit:cover;flex:none;border:2px solid var(--beacon)}
.ab-name{font:700 1.05rem var(--display);color:var(--ink)}
.ab-name a{color:var(--trail)}
.ab-bio{margin:.35rem 0 .7rem;color:var(--ink-soft);font-size:1rem;line-height:1.5}
.author-box .soc{display:flex;gap:.4rem}
.topic-strip{display:flex;flex-wrap:wrap;gap:.45rem}
.home-topics{margin:2.4rem 0 .4rem}
.topic-strip a{font:600 .85rem var(--display);padding:.45rem .85rem;border-radius:99px;border:1px solid var(--line);color:var(--ink-soft);transition:background .2s,color .2s,border-color .2s}
.topic-strip a:hover,.topic-strip a[aria-current]{background:var(--trail);border-color:var(--trail);color:var(--bg)}
.drawer-topics{margin:1.2rem 0 .4rem}
.drawer-topics p{font:700 .75rem var(--display);letter-spacing:.12em;text-transform:uppercase;color:var(--mute);margin-bottom:.6rem}
.page-head{margin:1rem 0 1.6rem}
.page-head h1{font:800 clamp(2rem,5vw,3rem)/1.05 var(--display);letter-spacing:-.035em}
.page-head p{margin-top:.6rem;color:var(--mute);font-size:1.05rem}
.page-head .topic-strip{margin-top:1.2rem}
.topic-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1.6rem 1.3rem}
.topic-grid .story-card p.dek{margin-top:.5rem;color:var(--mute);font-size:.98rem;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
@media (max-width:900px){.topic-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:560px){.topic-grid{grid-template-columns:1fr}}
.archive-month{font:800 1.25rem var(--display);margin:2rem 0 .6rem;padding-bottom:.4rem;border-bottom:1px solid var(--line)}
.archive-list{list-style:none;padding:0;margin:0}
.archive-list li{display:grid;grid-template-columns:5.5rem 1fr auto;gap:.9rem;align-items:baseline;padding:.6rem 0;border-bottom:1px dashed var(--line)}
.archive-list time{font:500 .85rem var(--display);color:var(--mute)}
.archive-list a{font:600 1.05rem/1.35 var(--display);color:var(--ink)}
.archive-list a:hover{color:var(--trail)}
.archive-list .vtag{position:static}
@media (max-width:560px){.archive-list li{grid-template-columns:1fr auto}.archive-list time{grid-column:1/-1}}
.search-btn{display:grid;place-items:center;width:38px;height:38px;border-radius:99px;border:1px solid var(--line);background:none;color:var(--ink);cursor:pointer}
.search-btn svg,.search-row svg{width:18px;height:18px}
@media (max-width:440px){.bar-right .nl-btn{display:none}.search-btn{width:34px;height:34px}}
.search-ov{position:fixed;inset:0;z-index:150;background:rgba(3,5,12,.6);display:flex;justify-content:center;align-items:flex-start;padding:12vh 1rem 1rem}
.search-ov[hidden]{display:none}
.search-box{width:100%;max-width:620px;background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:0 30px 80px rgba(0,0,0,.5);overflow:hidden}
.search-row{display:flex;align-items:center;gap:.7rem;padding:.9rem 1.1rem;border-bottom:1px solid var(--line);color:var(--mute)}
.search-row input{flex:1;min-width:0;background:none;border:0;color:var(--ink);font:500 1.1rem var(--display);outline:none}
.search-close{background:none;border:1px solid var(--line);border-radius:8px;color:var(--mute);font:600 .75rem var(--display);padding:.3rem .5rem;cursor:pointer}
.search-res{list-style:none;margin:0;padding:.4rem;max-height:60vh;overflow:auto}
.search-res a{display:flex;flex-direction:column;gap:.2rem;padding:.75rem .8rem;border-radius:12px}
.search-res a:hover,.search-res a:focus-visible{background:var(--bg)}
.sr-t{font:600 1rem/1.3 var(--display);color:var(--ink)}
.sr-m{font:500 .8rem var(--display);color:var(--mute)}
.sr-v{color:var(--vc)}
.sr-none{padding:1rem .8rem;color:var(--mute);font-size:.98rem}
.back-link{font:500 .9rem var(--display);color:var(--mute);display:inline-block;margin-bottom:1.6rem;transition:color .2s}
.back-link:hover{color:var(--trail)}
.post-meta{font:500 .85rem var(--display);color:var(--mute)}
.author-link{color:var(--ink);border-bottom:1px solid var(--line)}
.author-link:hover{color:var(--trail);border-bottom-color:var(--trail)}
.post-title{font:800 clamp(2rem,5vw,3rem)/1.05 var(--display);letter-spacing:-.035em;margin:.7rem 0 1.8rem}
.post-body{font-size:1.2rem;line-height:1.75}
.post-body h2{font:800 1.7rem/1.15 var(--display);letter-spacing:-.025em;margin:2em 0 .6em}
.post-body h3{font:800 1.3rem/1.2 var(--display);letter-spacing:-.02em;margin:1.7em 0 .6em}
.post-body p{margin:0 0 1.3em;color:var(--ink-soft)}
.post-body a{color:var(--trail);border-bottom:1px solid color-mix(in srgb,var(--trail) 45%,transparent)}
.post-body a:hover{border-bottom-color:var(--trail)}
.post-body blockquote{margin:1.6em 0;padding:.2rem 0 .2rem 1.2rem;border-left:3px solid var(--beacon);font-style:italic;color:var(--ink)}
.post-body pre{background:var(--code);border:1px solid var(--line);border-radius:10px;padding:1rem 1.2rem;overflow-x:auto;font:.9rem/1.6 ui-monospace,Menlo,monospace;margin:1.5em 0}
.post-body code{font:.85em ui-monospace,Menlo,monospace;background:var(--code);padding:.1rem .35rem;border-radius:5px}
.post-body pre code{background:none;padding:0}
.post-body ul{padding-left:1.3em;margin:0 0 1.3em;color:var(--ink-soft)}
.post-body img{margin:1.6em 0;border-radius:12px;border:1px solid var(--line)}
.post-hero{margin:0 0 2rem;border-radius:14px;overflow:hidden;border:1px solid var(--line);aspect-ratio:16/9;background:var(--panel)}
@media (min-width:700px){.post-hero{aspect-ratio:2.1/1}}
.post-hero img{width:100%;height:100%;object-fit:cover;margin:0}
.tags-row{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:1.6rem}
.tags-row a{font:500 .8rem var(--display);color:var(--trail);border:1px solid var(--line);padding:.3rem .7rem;border-radius:99px;transition:background .2s,color .2s}
.tags-row a:hover{background:var(--trail);color:var(--bg)}
.share-bar{margin-top:2.2rem;padding:1.3rem 1.4rem;background:var(--panel);border:1px solid var(--line);border-radius:18px}
.share-label{font:700 1rem var(--display);color:var(--ink);margin-bottom:.9rem}
.share-row{display:flex;flex-wrap:wrap;gap:.55rem}
.share-btn{display:inline-flex;align-items:center;gap:.45rem;padding:.55rem .9rem;border:1px solid var(--line);
  border-radius:99px;background:transparent;color:var(--ink-soft);font:600 .85rem var(--display);cursor:pointer;
  text-decoration:none;transition:background .2s,color .2s,border-color .2s}
.share-btn svg{width:16px;height:16px;flex:none}
.share-btn:hover,.share-btn:focus-visible{background:var(--beacon);border-color:var(--beacon);color:var(--on-beacon)}
.share-btn.copied{background:var(--trail);border-color:var(--trail);color:var(--bg)}
@media (max-width:480px){.share-btn span{display:none}.share-btn{padding:.65rem}.share-native span,.share-copy span{display:inline}}

footer.site-foot{display:flex;flex-wrap:wrap;gap:.8rem 1.6rem;padding-top:2rem;padding-bottom:3rem;color:var(--mute);font:500 .9rem var(--display)}
footer.site-foot a{transition:color .2s}
footer.site-foot a:hover{color:var(--ink)}
footer.site-foot .sp{flex:1}
.social-links{display:flex;gap:1.2rem;flex-wrap:wrap}

/* Newsletter: bottom band + popup */
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.newsletter-card{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:clamp(1.4rem,3vw,2.4rem)}
.newsletter-card__title{font:800 1.7rem/1.1 var(--display);letter-spacing:-.025em;margin-bottom:.5rem}
.newsletter-card__body{color:var(--mute);font-size:1.02rem;margin-bottom:1.2rem}
.newsletter-form{display:flex;gap:.6rem}
.newsletter-form input[type="email"]{flex:1;min-width:0;background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:.85rem 1rem;font:inherit;font-size:1rem}
.newsletter-form input[type="email"]:focus{outline:2px solid var(--trail);outline-offset:1px}
.newsletter-form button[type="submit"]{border:0;border-radius:12px;padding:.85rem 1.3rem;font:800 .95rem var(--display);background:var(--beacon);color:var(--on-beacon);cursor:pointer;white-space:nowrap;transition:transform .2s,background .3s}
.newsletter-form button[type="submit"]:active{transform:scale(.96)}
.newsletter-form button[type="submit"].is-done{background:var(--trail);pointer-events:none}
.nl-status{margin-top:.8rem;font-size:.95rem;color:var(--ink-soft);min-height:1.3em}
.nl-inline{padding:3rem 0 1rem}
.nl-inline .newsletter-card{display:flex;flex-wrap:wrap;gap:1.2rem 2rem;align-items:center;justify-content:space-between}
.nl-inline .nl-copy{flex:1 1 280px}
.nl-inline .nl-form{flex:1 1 340px;max-width:470px}
.nl-inline .newsletter-card__body{margin-bottom:0}

.nl-popup{position:fixed;inset:0;z-index:9999;display:none}
.nl-popup.is-visible{display:flex;align-items:center;justify-content:center;padding:1.2rem;overflow-y:auto}
.nl-popup__backdrop{position:absolute;inset:0;background:rgba(4,6,14,.72);backdrop-filter:blur(6px);animation:fade-in .35s both}
@keyframes fade-in{from{opacity:0}}
.nl-popup__card{width:100%;max-width:520px;z-index:1;box-shadow:0 30px 80px rgba(0,0,0,.5);overflow:hidden;animation:pop .6s cubic-bezier(.2,.9,.25,1.1) both}
@keyframes pop{from{opacity:0;transform:translateY(30px) scale(.94)}}
.nl-popup__card::before{content:"";position:absolute;left:0;right:0;top:0;height:3px;background:linear-gradient(90deg,var(--trail),var(--beacon));transform-origin:left;animation:sweep 1s .25s cubic-bezier(.2,.85,.2,1) both}
@keyframes sweep{from{transform:scaleX(0)}}
.nl-popup__card .newsletter-form{flex-wrap:wrap}
.nl-popup__card .newsletter-form input[type="email"]{min-width:200px}
.nl-popup__close{position:absolute;top:.8rem;right:1rem;background:none;border:0;font-size:1.8rem;line-height:1;cursor:pointer;color:var(--mute);transition:color .2s,transform .3s}
.nl-popup__close:hover{color:var(--beacon);transform:rotate(90deg)}
@media (max-width:600px){
  .newsletter-form{flex-direction:column}
  .newsletter-form button[type="submit"]{width:100%}
}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-delay:0s!important;transition-duration:.01ms!important}
  .hero h1 .c{transform:none}
  html{scroll-behavior:auto}
}
"""

# Runs in <head> so the right theme paints on the first frame. Dark is the default.
THEME_BOOT = ("try{var t=localStorage.getItem('theme');"
              "document.documentElement.setAttribute('data-theme',t==='light'?'light':'dark')}"
              "catch(e){}")

COMMON_JS = """
(function(){
  var n=document.querySelector('.share-native');
  if(n && navigator.share){ n.hidden=false;
    n.addEventListener('click',function(){ navigator.share({title:n.dataset.title,url:n.dataset.url}).catch(function(){}); }); }
  var c=document.querySelector('.share-copy');
  if(c){ c.addEventListener('click',function(){
    var done=function(){ var s=c.querySelector('span'); s.textContent='Copied!'; c.classList.add('copied');
      setTimeout(function(){ s.textContent='Copy link'; c.classList.remove('copied'); },1800); };
    if(navigator.clipboard){ navigator.clipboard.writeText(c.dataset.url).then(done,function(){ prompt('Copy this link:',c.dataset.url); }); }
    else { prompt('Copy this link:',c.dataset.url); }
  }); }
})();
(function(){
  var root=document.documentElement, reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
  var btn=document.getElementById('theme-toggle'), label=document.getElementById('theme-label');
  window.__tt={colors:{},redraw:function(){}};
  function readColors(){
    var cs=getComputedStyle(root), c=window.__tt.colors;
    c.bg=cs.getPropertyValue('--bg').trim(); c.trail=cs.getPropertyValue('--trail').trim(); c.beacon=cs.getPropertyValue('--beacon').trim();
    if(label) label.textContent=root.getAttribute('data-theme');
  }
  readColors();
  function setTheme(){
    var next=root.getAttribute('data-theme')==='dark'?'light':'dark';
    root.setAttribute('data-theme',next);
    try{localStorage.setItem('theme',next);}catch(e){}
    readColors(); window.__tt.redraw();
  }
  if(btn) btn.addEventListener('click',function(){
    if(!document.startViewTransition||reduce){setTheme();return;}
    var r=btn.getBoundingClientRect(), x=r.left+r.width/2, y=r.top+r.height/2;
    var end=Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y));
    document.startViewTransition(setTheme).ready.then(function(){
      root.animate({clipPath:['circle(0px at '+x+'px '+y+'px)','circle('+end+'px at '+x+'px '+y+'px)']},
        {duration:700,easing:'cubic-bezier(.2,.8,.2,1)',pseudoElement:'::view-transition-new(root)'});
    });
  });
  var head=document.querySelector('.site-head');
  function setHead(){ if(head) root.style.setProperty('--head-h',head.offsetHeight+'px'); }
  setHead(); addEventListener('resize',setHead);
  var burger=document.getElementById('burger'), drawer=document.getElementById('drawer'), scrim=document.getElementById('drawer-scrim');
  function setDrawer(open){
    drawer.classList.toggle('open',open); scrim.classList.toggle('open',open);
    burger.setAttribute('aria-expanded',open?'true':'false');
    burger.setAttribute('aria-label',open?'Close menu':'Open menu');
    document.body.style.overflow=open?'hidden':'';
  }
  if(burger&&drawer&&scrim){
    burger.addEventListener('click',function(){setDrawer(burger.getAttribute('aria-expanded')!=='true');});
    scrim.addEventListener('click',function(){setDrawer(false);});
    drawer.addEventListener('click',function(e){ if(e.target.closest('a,[data-nl-open]')) setDrawer(false); });
    addEventListener('keydown',function(e){
      if(e.key==='Escape'&&burger.getAttribute('aria-expanded')==='true'){setDrawer(false);burger.focus();}
    });
  }
  var prog=document.querySelector('.progress'), maxScroll=0, ticking=false;
  function measure(){ maxScroll=root.scrollHeight-innerHeight; }
  function paint(){ ticking=false; if(prog) prog.style.setProperty('--p',maxScroll>0?Math.min(1,scrollY/maxScroll):0); }
  function onScroll(){ if(!ticking){ ticking=true; requestAnimationFrame(paint); } }
  measure(); paint();
  addEventListener('scroll',onScroll,{passive:true});
  addEventListener('resize',function(){measure();onScroll();});
  addEventListener('load',function(){measure();onScroll();});
  if(window.ResizeObserver) new ResizeObserver(function(){measure();onScroll();}).observe(document.body);
  var top=document.querySelector('.to-top');
  if(top){
    addEventListener('scroll',function(){top.classList.toggle('show',scrollY>900);},{passive:true});
    top.addEventListener('click',function(e){e.preventDefault();scrollTo({top:0,behavior:'smooth'});});
  }
  var links=[].slice.call(document.querySelectorAll('.toc-side a'));
  if(links.length&&window.IntersectionObserver){
    var map={};
    links.forEach(function(a){map[a.getAttribute('href').slice(1)]=a;});
    var io=new IntersectionObserver(function(es){
      es.forEach(function(e){
        if(!e.isIntersecting) return;
        links.forEach(function(a){a.classList.remove('on');});
        if(map[e.target.id]) map[e.target.id].classList.add('on');
      });
    },{rootMargin:'-20% 0px -70% 0px'});
    Object.keys(map).forEach(function(id){var h=document.getElementById(id); if(h) io.observe(h);});
  }
  var ov=document.getElementById('search-ov'), q=document.getElementById('search-q'), res=document.getElementById('search-res'),
      sbtn=document.querySelector('[data-search-open]'), data=null, base=sbtn?sbtn.getAttribute('data-base'):'';
  function esc(t){return t.replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function load(){ if(data) return Promise.resolve(data);
    return fetch(base+'search.json').then(function(r){return r.json();}).then(function(d){data=d;return d;}); }
  function render(){
    var words=q.value.toLowerCase().trim().split(/\\s+/).filter(Boolean);
    if(!words.length){res.innerHTML='';return;}
    load().then(function(d){
      var hits=d.filter(function(p){var h=(p.t+' '+p.s+' '+p.k).toLowerCase();return words.every(function(w){return h.indexOf(w)>-1;});}).slice(0,8);
      res.innerHTML=hits.length?hits.map(function(p){
        return '<li><a href="'+base+p.u+'"><span class="sr-t">'+esc(p.t)+'</span><span class="sr-m">'+esc(p.d)+
          (p.v?' &middot; <b class="sr-v v-'+p.v.toLowerCase()+'">'+p.v+'</b>':'')+'</span></a></li>';}).join('')
        :'<li class="sr-none">No posts match "'+esc(q.value)+'".</li>';
    }).catch(function(){res.innerHTML='<li class="sr-none">Search is unavailable right now.</li>';});
  }
  function openS(){ if(!ov) return; ov.hidden=false; document.body.style.overflow='hidden'; setTimeout(function(){q.focus();},20); load(); }
  function closeS(){ if(!ov) return; ov.hidden=true; document.body.style.overflow=''; if(sbtn) sbtn.focus(); }
  if(ov){
    sbtn&&sbtn.addEventListener('click',openS);
    q.addEventListener('input',render);
    ov.addEventListener('click',function(e){ if(e.target===ov||e.target.closest('.search-close')) closeS(); });
    addEventListener('keydown',function(e){
      if(e.key==='Escape'&&!ov.hidden) closeS();
      if(e.key==='/'&&ov.hidden&&!/input|textarea/i.test((document.activeElement||{}).tagName||'')){e.preventDefault();openS();}
    });
  }
})();
"""

# Homepage only: headline entrance and ridge-line terrain.
HERO_JS = """
(function(){
  var reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
  var h=document.getElementById('title'), n=0;
  if(h){
    var words=h.textContent.split(' '); h.textContent='';
    words.forEach(function(wd){
      var w=document.createElement('span'); w.className='w'; w.setAttribute('aria-hidden','true');
      wd.split('').forEach(function(ch){var c=document.createElement('span'); c.className='c'; c.style.setProperty('--i',n++); c.textContent=ch; w.appendChild(c);});
      h.appendChild(w); h.appendChild(document.createTextNode(' '));
    });
  }
  var c=document.getElementById('ridge'), hero=document.getElementById('hero');
  if(c&&hero){
    var g=c.getContext('2d'), colors=window.__tt.colors;
    var W,H,dpr,t=0,mx=-999,my=-999,tx=-999,ty=-999,visible=true;
    var size=function(){dpr=Math.min(devicePixelRatio||1,1.5);W=c.clientWidth;H=c.clientHeight;c.width=W*dpr;c.height=H*dpr;g.setTransform(dpr,0,0,dpr,0,0);};
    var height=function(nx,r){
      var env=Math.exp(-Math.pow((nx-.7)*2.6,2));
      return (Math.sin(nx*9+r*.33+t*3)*.5+Math.sin(nx*17-r*.21+t*2)*.3+Math.sin(nx*4+t)*.2)*58*env;
    };
    var draw=function(){
      var rows=Math.max(22,Math.round(H/18)), step=W<600?14:9, bx=0, by=0, near=mx>-500;
      g.clearRect(0,0,W,H);
      mx+=(tx-mx)*.12; my+=(ty-my)*.12;
      var beaconRow=Math.round(rows*.42);
      for(var r=0;r<rows;r++){
        var y0=H*.2+r*(H*.78/rows), ys=[], px, y;
        for(px=0;px<=W;px+=step){
          y=y0-height(px/W,r);
          if(near){ var dx=px-mx, dy=y0-my, d2=dx*dx+dy*dy; if(d2<90000) y-=Math.exp(-d2/9000)*38; }
          ys.push(y);
          if(r===beaconRow&&Math.abs(px/W-.7)<step/W/2+.001){bx=px;by=y;}
        }
        g.beginPath(); g.moveTo(0,ys[0]);
        for(var k=1;k<ys.length;k++) g.lineTo(k*step,ys[k]);
        g.lineTo(W,H); g.lineTo(0,H); g.closePath();
        g.fillStyle=colors.bg; g.fill();
        g.globalAlpha=.18+.72*(r/rows); g.strokeStyle=colors.trail; g.lineWidth=1.2;
        g.beginPath(); g.moveTo(0,ys[0]);
        for(k=1;k<ys.length;k++) g.lineTo(k*step,ys[k]);
        g.stroke(); g.globalAlpha=1;
      }
      var p=(t*40)%1;
      g.fillStyle=colors.beacon; g.beginPath(); g.arc(bx,by,4.5,0,6.283); g.fill();
      g.globalAlpha=1-p; g.strokeStyle=colors.beacon; g.lineWidth=1.5; g.beginPath(); g.arc(bx,by,4+p*18,0,6.283); g.stroke(); g.globalAlpha=1;
    };
    var last=0, running=false;
    var loop=function(now){
      if(!visible||document.hidden){ running=false; return; }
      requestAnimationFrame(loop);
      if(now-last<32) return;               // about 30 frames a second is plenty for a slow wave
      t+=.0035*Math.min(3,(now-last)/16.7||2); last=now; draw();
    };
    var start=function(){ if(!running&&!reduce&&visible&&!document.hidden){ running=true; last=0; requestAnimationFrame(loop); } };
    document.addEventListener('visibilitychange',start);
    size(); addEventListener('resize',function(){size(); if(reduce)draw();});
    hero.addEventListener('pointermove',function(e){var b=c.getBoundingClientRect();tx=e.clientX-b.left;ty=e.clientY-b.top;});
    hero.addEventListener('pointerleave',function(){tx=ty=-999;});
    new IntersectionObserver(function(en){visible=en[0].isIntersecting; start();}).observe(hero);
    window.__tt.redraw=function(){ if(reduce) draw(); };
    if(reduce){draw();} else {start();}
  }
})();
"""

# ---------------------------------------------------------------------------
# Newsletter signup — a centered popup PLUS a signup band at the bottom of
# every page. Both forms post to the Kit form (KIT_FORM_ID above).
# Kit expects the email field to be named exactly "email_address".
# ---------------------------------------------------------------------------

NEWSLETTER_SCRIPT = """
<script>
(function(){
  var DISMISS_KEY='techtreck_nl_dismissed_at', SUBSCRIBED_KEY='techtreck_nl_subscribed';
  var SHOW_AFTER_MS=8000, RESHOW_AFTER_DAYS=14;
  function daysSince(ts){return (Date.now()-Number(ts))/(1000*60*60*24);}
  function shouldShow(){
    try{
      if(localStorage.getItem(SUBSCRIBED_KEY)==='true') return false;
      var d=localStorage.getItem(DISMISS_KEY);
      if(d && daysSince(d)<RESHOW_AFTER_DAYS) return false;
    }catch(e){}
    return true;
  }
  function openPopup(){var el=document.getElementById('newsletter-popup'); if(el){el.classList.add('is-visible'); el.setAttribute('aria-hidden','false');}}
  function closePopup(){var el=document.getElementById('newsletter-popup'); if(el){el.classList.remove('is-visible'); el.setAttribute('aria-hidden','true'); try{localStorage.setItem(DISMISS_KEY,String(Date.now()));}catch(e){}}}
  document.addEventListener('DOMContentLoaded', function(){
    if(shouldShow()) setTimeout(openPopup, SHOW_AFTER_MS);
    document.querySelectorAll('[data-nl-close]').forEach(function(btn){btn.addEventListener('click', closePopup);});
    document.querySelectorAll('[data-nl-open]').forEach(function(btn){btn.addEventListener('click', function(e){e.preventDefault(); openPopup();});});
    document.addEventListener('keydown', function(e){ if(e.key==='Escape') closePopup(); });
    document.querySelectorAll('.newsletter-form').forEach(function(form){
      form.addEventListener('submit', function(e){
        e.preventDefault();
        var box = form.closest('.newsletter-card');
        var status = box.querySelector('.nl-status');
        var button = form.querySelector('button[type="submit"]');
        if(status) status.textContent = 'Subscribing...';
        fetch(form.action, {method:'POST', body:new FormData(form), headers:{'Accept':'application/json'}})
          .then(function(res){
            if(!res.ok){ throw new Error('bad status'); }
            if(status) status.textContent = 'Almost there! Check your email to confirm your subscription.';
            if(button){ button.textContent = 'You’re in'; button.classList.add('is-done'); }
            try{ localStorage.setItem(SUBSCRIBED_KEY,'true'); }catch(e){}
            setTimeout(closePopup, 3500);
          })
          .catch(function(){ if(status) status.textContent = 'Something went wrong. Please try again.'; });
      });
    });
  });
})();
</script>
"""

POPUP_HTML = """
<div id="newsletter-popup" class="nl-popup" aria-hidden="true">
  <div class="nl-popup__backdrop" data-nl-close></div>
  <div class="newsletter-card nl-popup__card" role="dialog" aria-labelledby="nl-popup-title">
    <button type="button" class="nl-popup__close" data-nl-close aria-label="Close">&times;</button>
    <h3 id="nl-popup-title" class="newsletter-card__title">Get TechByDave in your inbox</h3>
    <p class="newsletter-card__body">One short daily digest of what got published. No spam, unsubscribe anytime.</p>
    <form class="newsletter-form" action="__KIT_FORM_ACTION__" method="post">
      <label for="nl-email-popup" class="sr-only">Email address</label>
      <input id="nl-email-popup" type="email" name="email_address" placeholder="you@example.com" required />
      <button type="submit">Subscribe</button>
    </form>
    <p class="nl-status"></p>
  </div>
</div>
""".replace("__KIT_FORM_ACTION__", KIT_FORM_ACTION) + NEWSLETTER_SCRIPT

INLINE_SIGNUP_HTML = """
<section class="nl-inline wrap" id="newsletter">
  <div class="newsletter-card">
    <div class="nl-copy">
      <h3 class="newsletter-card__title">Get TechByDave in your inbox</h3>
      <p class="newsletter-card__body">One short daily digest of what got published. No spam, unsubscribe anytime.</p>
    </div>
    <div class="nl-form">
      <form class="newsletter-form" action="__KIT_FORM_ACTION__" method="post">
        <label for="nl-email-inline" class="sr-only">Email address</label>
        <input id="nl-email-inline" type="email" name="email_address" placeholder="you@example.com" required />
        <button type="submit">Subscribe</button>
      </form>
      <p class="nl-status"></p>
    </div>
  </div>
</section>
""".replace("__KIT_FORM_ACTION__", KIT_FORM_ACTION)

# ---------------------------------------------------------------------------
# Static page copy
# ---------------------------------------------------------------------------

ABOUT_MARKDOWN = f"""{SITE_TITLE} answers one question about every new gadget: **is it worth it, or a waste of money?** We cover phones, laptops, wearables, headphones, smart home gear, gaming hardware and cameras with an honest verdict on whether to buy, wait or skip, plus smarter ways to spend: deals, cheaper alternatives, and when the older model is the better buy.

## Hi, I'm {AUTHOR_NAME}

I'm the founder of {SITE_TITLE}, based in Toronto. I started this site because I love tech, and I wanted a place that tells people what actually matters about a new gadget: what it costs, what changed, and whether it's worth their money. Tech moves fast, and it's easy to get lost in hype, spec sheets and marketing. My job is to cut through that and give you a straight answer.

## What I do

I follow what's happening in consumer tech and turn it into posts you can read in a few minutes. That means covering new phone and laptop launches, tracking price drops and deals, comparing new models with the ones they replace, and helping you decide when to upgrade and when to wait.

Each post ends with **My take**, where I share my honest opinion: who should buy it, who should skip it, and whether the deal is really a deal. I also share {SITE_TITLE} on TikTok, Instagram, YouTube, X and Pinterest, and send a daily newsletter so you never miss a story.

## Hands-on reviews

Some posts are **hands-on reviews** of gear I own and use myself, like the phone and kit I film TechByDave videos with. These are marked with a "Hands-on review" badge, use my own photos, and tell you what living with the product is actually like: what's great, what annoys me, and whether I'd buy it again.

## My mission

My mission is to put out the best tech content and blogs I can: clear, honest and genuinely useful. No clickbait, no filler, and no pretending a product is great just because it's new. If something is a steal, I'll say so. If it isn't worth the upgrade, I'll say that too.

## My vision

My vision is to grow {SITE_TITLE} into a trusted name in tech and build an audience of people who come here first when they want to know what to buy. But for me, this is about more than gadgets. As {SITE_TITLE} grows, I want to use it to launch a foundation that helps people in need all over the world. Every reader, subscriber and share helps bring that goal closer.

## How posts are made

News posts are researched and drafted with the help of AI tools working from published sources, then I add my own photos and my own take before anything goes live. Hands-on reviews are written by me from my own use of the product. Every post lists its sources at the bottom so you can check the facts yourself. We state only what those sources confirm, and we never claim to have tested a product we haven't. Read the full [editorial policy](editorial-policy.html).

## Newsletter

Want it without checking back constantly? Subscribe below for a once-daily digest of everything published that day: one email, no spam.

## Affiliate links

Some posts link to products through the Amazon Associates program. When they do, it's always disclosed in the post, never disguised as a regular link. Using those links supports the site at no extra cost to you.

## Work with {SITE_TITLE}

Want to see the gadgets I'd actually recommend? Browse my picks on [my Amazon storefront]({AMAZON_STOREFRONT}): gadgets worth the money, cheaper tech that does the job, and my iPhone filming setup.

Are you a brand or PR team? See the [media kit](media-kit.html) for product reviews, launch coverage, newsletter sponsorships and how to send review units.

## Get in touch

Have a tip, a product you want covered, or a correction? Email me at [{AUTHOR_EMAIL}](mailto:{AUTHOR_EMAIL}). I read every message."""

PRIVACY_MARKDOWN = f"""This page explains what data {SITE_TITLE} collects and why, in plain language.

## Advertising

This site uses Google AdSense to display ads. Google and its partners may use cookies and similar technologies to serve ads based on your visits to this and other sites. You can opt out of personalized advertising by visiting [Google's Ads Settings](https://adssettings.google.com/) or [aboutads.info](https://www.aboutads.info/choices/).

## Analytics

This site uses Cloudflare Web Analytics to understand overall traffic (page views, referring sites). It does not use cookies and does not track individual visitors — it's built specifically to be privacy-friendly.

## Affiliate links

Some posts contain Amazon Associates affiliate links. If you click one and make a purchase, {SITE_TITLE} may earn a small commission at no extra cost to you. This is always disclosed within the post itself.

## Newsletter

If you subscribe to the newsletter, your email address is stored with our email service provider, Kit, and used only to send you the daily digest. It is never sold or shared with third parties. You can unsubscribe at any time using the link in every email.

## Contact

Questions about this policy? Email [{AUTHOR_EMAIL}](mailto:{AUTHOR_EMAIL})."""

EDITORIAL_MARKDOWN = f"""This page explains how {SITE_TITLE} decides what to cover, where the information comes from, and how posts are written.

## What we cover

Consumer tech people actually buy: phones, laptops, wearables, headphones, smart home gear, gaming hardware, and cameras. We focus on launches, notable price changes, spec comparisons, and buying advice: whether a product is worth it, and whether there's a smarter way to spend the money. Being based in Canada, we add Canadian prices and availability when sources confirm them.

## How stories are chosen

Each day we look at what is trending in consumer tech and pick one story that we haven't already covered. We skip stories that don't connect to a product people can buy.

## Sources and accuracy

Every post lists the pages its facts came from in a Sources line at the bottom. A post may only state facts found in those sources. If a price or spec isn't confirmed, we leave it out or say it isn't confirmed yet.

## How posts are written

Our news posts are researched and drafted with the help of AI tools. The tools search the web and write from the sources they find. These posts are not hands-on reviews: we haven't tested a product unless a post explicitly says so, and we don't invent quotes or personal experiences. Hands-on reviews carry a "Hands-on review" badge and are written by Dave from his own use of the product.

## Affiliate links and ads

Some posts contain Amazon Associates affiliate links, and we may earn a small commission if you buy through them, at no extra cost to you. This is disclosed in the post. Some pages may also show ads through Google AdSense. Affiliate links and ads don't decide which stories we cover.

## Corrections

If you spot a mistake, email [{AUTHOR_EMAIL}](mailto:{AUTHOR_EMAIL}) with a link to the post. We fix confirmed errors and note significant corrections in the post.

## Who to contact

{AUTHOR_NAME} runs {SITE_TITLE}. See the [contact page](contact.html) for how to reach us."""

CONTACT_MARKDOWN = f"""Questions, corrections, story tips, or partnership requests? Email us:

- {AUTHOR_NAME}: [{AUTHOR_EMAIL}](mailto:{AUTHOR_EMAIL})
- General information: [{INFO_EMAIL}](mailto:{INFO_EMAIL})

Brands and PR teams: see our [media kit](media-kit.html) for reviews, launch coverage and sponsorships.

You can also find {SITE_TITLE} on social media through the links in the footer. For how we choose and write our stories, see the [editorial policy](editorial-policy.html)."""

MEDIA_KIT_MARKDOWN = f"""{SITE_TITLE} is an independent consumer tech publication run by {AUTHOR_NAME} out of Toronto, Canada. Our angle is simple: is it worth it, and is there a smarter way to spend the money? We cover the gadgets people actually buy: phones, laptops, wearables, headphones, smart home gear, gaming hardware, cameras and everyday-carry tech.

## Who reads {SITE_TITLE}

Our readers are people about to buy something. They come to {SITE_TITLE} to find out what changed, what it costs, and whether it's worth the upgrade.

- **Social followers:** 3,000+ across TikTok, Instagram, YouTube, X and Pinterest
- **Daily newsletter:** a once-a-day digest email sent to subscribers
- **Publishing:** a new story every day, plus in-depth buying guides

## Ways to work with us

- **Product reviews:** a hands-on written review plus a short video for TikTok, Instagram Reels and YouTube Shorts.
- **Launch coverage:** we cover new products on launch day and respect embargoes.
- **Buying guides:** we consider products for guides such as best new phones and best everyday-carry gadgets.
- **Newsletter sponsorship:** one sponsored spot in the daily digest email, clearly labeled.
- **Sponsored posts and videos:** a dedicated piece about your product, always labeled as sponsored.

## Our review promise

We tell readers what we really think. Sending a product doesn't buy a positive review, and brands don't see or approve coverage before it goes live. Sponsored content and affiliate links are always disclosed. We think that honesty is exactly what makes our coverage worth having. Read our [editorial policy](editorial-policy.html).

## Sending products

Review units can be shipped to Toronto, Ontario. Let us know whether you'd like the unit returned after review, and we'll arrange it.

## Rates

Rates for sponsorships and sponsored content are available on request.

## Get in touch

Email {AUTHOR_NAME} at [{AUTHOR_EMAIL}](mailto:{AUTHOR_EMAIL}) with your product, launch date and what you have in mind. We usually reply within two business days."""


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

FONTS_URL = ("https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,600;12..96,800"
             "&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap")

SUN_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">'
            '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')


def footer_links(prefix):
    pages = [("all posts", "archive.html"), ("privacy", "privacy.html"), ("editorial", "editorial-policy.html"),
             ("contact", "contact.html"), ("media kit", "media-kit.html"),
             ("shop my picks", AMAZON_STOREFRONT)]
    return "".join(_link(prefix, href, label) for label, href in pages)


def render_social_links():
    return "".join(
        f'<a href="{html.escape(url.strip())}">{html.escape(label)}</a>'
        for label, url in SOCIAL_LINKS
    )


# Icons for the header and menu. Keys match the labels in SOCIAL_LINKS
# (case-insensitive). The bool says whether the icon is filled (True) or
# drawn as an outline (False). Unknown labels fall back to their first letter.
ICONS = {
    "instagram": ('<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/>'
                  '<circle cx="17.5" cy="6.5" r=".6"/>', False),
    "x": ('<path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835'
          'L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/>', True),
    "youtube": ('<path d="M2.5 17a24.1 24.1 0 0 1 0-10 2 2 0 0 1 1.4-1.4 49.6 49.6 0 0 1 16.2 0A2 2 0 0 1 21.5 7'
                'a24.1 24.1 0 0 1 0 10 2 2 0 0 1-1.4 1.4 49.6 49.6 0 0 1-16.2 0A2 2 0 0 1 2.5 17"/>'
                '<path d="m10 15 5-3-5-3z"/>', False),
    "tiktok": ('<path d="M9 12a4 4 0 1 0 4 4V4a5 5 0 0 0 5 5"/>', False),
    # A simple push-pin outline, drawn in the same style as the other icons.
    "pinterest": ('<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16'
                  'a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7'
                  'a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>', False),
}

SEARCH_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               'aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>')
MAIL_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
             'stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/>'
             '<path d="m3 7 9 6 9-6"/></svg>')


SHARE_ICONS = {
    "share": '<path d="M4 12v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7"/><path d="m16 6-4-4-4 4"/><path d="M12 2v13"/>',
    "facebook": '<path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z"/>',
    "whatsapp": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>',
    "reddit": '<circle cx="12" cy="14" r="7"/><circle cx="18.5" cy="5.5" r="1.5"/><path d="m12 7 1.5-4 5 2.5"/>'
              '<path d="M9.5 16.5c1.5 1 3.5 1 5 0"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
            '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
}


def render_share_bar(url, title, image=""):
    """Share buttons at the end of every post. Plain links, so they work with no
    accounts, no tracking scripts and no outside service."""
    q = urllib.parse.quote
    u, t = q(url, safe=""), q(title, safe="")
    stroke = 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'

    def svg(inner, filled=False):
        attrs = 'fill="currentColor"' if filled else stroke
        return f'<svg viewBox="0 0 24 24" {attrs} aria-hidden="true">{inner}</svg>'

    pin = f"https://www.pinterest.com/pin/create/button/?url={u}&description={t}" + (f"&media={q(image, safe='')}" if image else "")
    links = [
        ("X", f"https://x.com/intent/post?text={t}&url={u}", svg(ICONS["x"][0], True)),
        ("Facebook", f"https://www.facebook.com/sharer/sharer.php?u={u}", svg(SHARE_ICONS["facebook"])),
        ("Pinterest", pin, svg(ICONS["pinterest"][0])),
        ("WhatsApp", f"https://wa.me/?text={t}%20{u}", svg(SHARE_ICONS["whatsapp"])),
        ("Reddit", f"https://www.reddit.com/submit?url={u}&title={t}", svg(SHARE_ICONS["reddit"])),
    ]
    a = "".join(f'<a class="share-btn" href="{html.escape(h)}" target="_blank" rel="noopener" '
                f'aria-label="Share on {n}" title="Share on {n}">{i}<span>{n}</span></a>' for n, h, i in links)
    data = f'data-url="{html.escape(url)}" data-title="{html.escape(title)}"'
    return (f'<section class="share-bar" aria-label="Share this post">'
            f'<div class="share-label">Enjoyed this? Share it</div><div class="share-row">'
            f'<button type="button" class="share-btn share-native" hidden {data}>{svg(SHARE_ICONS["share"])}<span>Share</span></button>'
            f'{a}'
            f'<button type="button" class="share-btn share-copy" {data}>{svg(SHARE_ICONS["link"])}<span>Copy link</span></button>'
            f'</div></section>')


def render_social_icons():
    out = []
    for label, url in SOCIAL_LINKS:
        u, l = html.escape(url.strip()), html.escape(label)
        inner, filled = ICONS.get(label.strip().lower(), (None, False))
        if inner is None:
            out.append(f'<a href="{u}" aria-label="{l}" rel="noopener">{html.escape(label[:1].upper())}</a>')
            continue
        attrs = ('fill="currentColor"' if filled else
                 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"')
        out.append(f'<a href="{u}" aria-label="{l}" title="{l}" rel="noopener">'
                   f'<svg viewBox="0 0 24 24" {attrs}>{inner}</svg></a>')
    return "".join(out)


def _link(prefix, href, label, cls=""):
    """Internal pages get the relative prefix; outside links (like the Amazon
    storefront) open in a new tab and are marked as sponsored for Google."""
    c = f' class="{cls}"' if cls else ""
    if href.startswith("http"):
        return f'<a{c} href="{href}" target="_blank" rel="sponsored noopener">{label}</a>'
    return f'<a{c} href="{prefix}{href}">{label}</a>'


def render_drawer(prefix):
    big = [("Latest", "index.html"), ("All posts", "archive.html"), ("About", "about.html"),
           ("Editorial policy", "editorial-policy.html"), ("Contact", "contact.html"),
           ("Media kit", "media-kit.html"), ("Shop my picks", AMAZON_STOREFRONT)]
    small = [("Privacy policy", "privacy.html"), ("RSS feed", "feed.xml")]
    big_html = "".join(_link(prefix, href, label, "item").replace('class="item"', f'class="item" style="--i:{i}"', 1)
                       for i, (label, href) in enumerate(big))
    small_html = "".join(f'<a class="item" style="--i:{len(big) + 1 + i}" href="{prefix}{href}">{label}</a>'
                         for i, (label, href) in enumerate(small))
    social = f'<div class="soc">{render_social_icons()}</div>' if SOCIAL_LINKS else ""
    return f"""
<div class="drawer-scrim" id="drawer-scrim"></div>
<aside class="drawer" id="drawer" aria-label="Site menu">
  <nav class="drawer-nav">{big_html}</nav>
  <div class="drawer-topics item" style="--i:{len(big)}"><p>Topics</p>{render_topic_strip(prefix)}</div>
  <div class="drawer-mid item" style="--i:{len(big)}">
    {social}
    <a class="nl-btn" href="#newsletter" data-nl-open>{MAIL_ICON}<span class="lbl">Newsletter</span></a>
  </div>
  <nav class="drawer-small">{small_html}</nav>
</aside>
"""


def render_page(*, title, description, canonical, prefix="", body, hero="", main_class="", image=None,
                og_type="website", head_extra=""):
    """Wrap `body` in the full site chrome. prefix is "" on root pages and "../" on post pages."""
    js = COMMON_JS + (HERO_JS if hero else "")
    og_image = ""
    if not image and os.path.isfile(os.path.join(IMAGES_DIR, DEFAULT_SHARE_IMAGE)):
        image = IMG_MARK + DEFAULT_SHARE_IMAGE  # pages and posts without a photo still get a preview picture
    if image:
        full = html.escape(fix_images(image, SITE_URL.rstrip("/") + "/"))
        og_image = f'<meta property="og:image" content="{full}"/>\n<meta name="twitter:image" content="{full}"/>'
    body = fix_images(body, prefix)
    hero = fix_images(hero, prefix)
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<title>{title}</title>
<meta name="description" content="{description}"/>
<link rel="canonical" href="{canonical}"/>
<meta name="robots" content="max-image-preview:large"/>
<meta property="og:type" content="{og_type}"/>
<meta property="og:site_name" content="{SITE_TITLE}"/>
<meta property="og:title" content="{title}"/>
<meta property="og:description" content="{description}"/>
<meta property="og:url" content="{canonical}"/>
{og_image}
<meta name="twitter:card" content="{'summary_large_image' if image else 'summary'}"/>
<meta name="twitter:title" content="{title}"/>
<meta name="twitter:description" content="{description}"/>
<meta name="google-site-verification" content="g5gG3v7FtM3b4ZUF9BsgGvIpjq6fgF2NK20e-5bTyDk" />
<meta name="impact-site-verification" value="6a4a9247-21ee-4a85-be00-2e3d141b9103" />
<meta name="google-adsense-account" content="ca-pub-4303818032223917">
<meta name="p:domain_verify" content="57d395b44f589a5e5f3ada79462de2c4"/>
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-4303818032223917"
     crossorigin="anonymous"></script>
<link rel="alternate" type="application/rss+xml" title="{SITE_TITLE}" href="/feed.xml"/>
<link rel="icon" href="{prefix}favicon.ico" sizes="48x48"/>
<link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml"/>
<link rel="icon" href="{prefix}favicon-32.png" type="image/png" sizes="32x32"/>
<link rel="icon" href="{prefix}icon-192.png" type="image/png" sizes="192x192"/>
<link rel="apple-touch-icon" href="{prefix}apple-touch-icon.png"/>
<meta name="theme-color" content="#070a14"/>
<link rel="manifest" href="{prefix}manifest.webmanifest"/>
{head_extra}
<script>{THEME_BOOT}</script>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link rel="stylesheet" href="{FONTS_URL}" media="print" onload="this.media='all'"/>
<noscript><link rel="stylesheet" href="{FONTS_URL}"/></noscript>
<style>{CSS}</style>
{_analytics_snippet()}
</head>
<body>
<a class="skip-link" href="#main">Skip to content</a>
<div class="progress" aria-hidden="true"><i></i><b></b></div>
<header class="site-head">
  <div class="wrap bar">
    <div class="bar-left">
      <button id="burger" class="burger" type="button" aria-label="Open menu" aria-expanded="false" aria-controls="drawer"><span></span><span></span><span></span></button>
      <a class="brand" href="{prefix}index.html"><span class="dot"></span>{SITE_TITLE}</a>
    </div>
    <div class="bar-right">
      <div class="soc soc-head">{render_social_icons()}</div>
      <button class="search-btn" type="button" aria-label="Search" data-search-open data-base="{prefix}">{SEARCH_ICON}</button>
      <a class="nl-btn" href="#newsletter" data-nl-open>{MAIL_ICON}<span class="lbl">Newsletter</span></a>
      <button id="theme-toggle" type="button" aria-label="Switch theme">{SUN_ICON}<span id="theme-label">dark</span></button>
    </div>
  </div>
</header>
{render_drawer(prefix)}
{hero}
<main id="main" tabindex="-1" class="wrap {main_class}">
{body}
</main>
{INLINE_SIGNUP_HTML}
<footer class="wrap site-foot">
  <span>&#169; {SITE_TITLE}</span>
  {footer_links(prefix)}
  <span class="sp"></span>
  <span class="social-links">{render_social_links()}</span>
</footer>
<div class="search-ov" id="search-ov" hidden>
  <div class="search-box" role="dialog" aria-modal="true" aria-label="Search {SITE_TITLE}">
    <div class="search-row">{SEARCH_ICON}<input id="search-q" type="search" placeholder="Search posts: iPhone, AirTag, earbuds…" autocomplete="off" aria-label="Search posts"/>
    <button type="button" class="search-close" aria-label="Close search">Esc</button></div>
    <ul class="search-res" id="search-res" aria-live="polite"></ul>
  </div>
</div>
<script>{js}</script>
{POPUP_HTML}
</body>
</html>
"""


def render_feed_item(meta, href):
    tag_list = [t.strip() for t in meta["tags"].split(",") if t.strip()]
    tag = f"<em>{html.escape(tag_list[0])}</em>" if tag_list else ""
    snippet = f"<p>{html.escape(meta['snippet'])}</p>" if meta["snippet"] else ""
    return (f'<li><a href="{href}"><time>{fmt_date_human(meta["_date_obj"])}{tag}</time>'
            f'<h3>{html.escape(meta["title"])}</h3>{snippet}</a></li>')


# ---------------------------------------------------------------------------
# Homepage story cards (Engadget-style layout)
# ---------------------------------------------------------------------------

_BODY_IMG_RE = re.compile(r'<img src="([^"]+)"')


def post_image(meta):
    """Cover image for a post: `image:` frontmatter, else first image in the body, else None."""
    if meta.get("image", "").strip():
        return resolve_image_src(meta["image"])
    m = _BODY_IMG_RE.search(meta.get("body_html", ""))
    return m.group(1) if m else None


# Clean topics for navigation. A post's free-form tags (and its title) are
# matched against these keywords, so new tags from the daily writer still land
# in the right topic. Order = menu order.
TOPICS = [
    ("Phones", "phones", ("phone", "smartphone", "iphone", "pixel", "android", "foldable", "motorola")),
    ("Audio", "audio", ("audio", "earbud", "headphone", "walkman", "dap", "turntable", "hi-fi", "phono", "speaker", "homepod", "home pod")),
    ("Smart Home", "smart-home", ("smart home", "smarthome", "alexa", "echo", "homepod", "home pod", "tracker", "airtag", "doorbell")),
    ("Wearables", "wearables", ("wearable", "ring", "watch", "fitness")),
    ("Computers & Tablets", "computers", ("laptop", "macbook", "imac", "ipad", "tablet", "gpu", "pc")),
    ("Gaming", "gaming", ("gaming", "console", "retro", "vr", "quest", "gpu")),
    ("TVs & Monitors", "tvs-monitors", ("tv", "monitor", "home entertainment")),
    ("Apple", "apple", ("apple", "iphone", "ipad", "macbook", "airtag", "homepod", "home pod", "imac")),
    ("Deals", "deals", ("deal", "black friday", "blackfriday", "sale")),
]
# Only these product words count when they appear in a title (tags always count),
# so "Cuts the Phone From AI" doesn't file a smart ring under Phones.
TITLE_KEYS = {"iphone", "pixel", "earbud", "headphone", "walkman", "turntable", "homepod", "echo",
              "doorbell", "macbook", "ipad", "laptop", "quest", "console", "deal", "black friday", "monitor"}
MENU_TOPICS = ["phones", "audio", "smart-home", "wearables", "computers", "gaming", "deals"]


def topics_for(meta):
    tags = [t.strip().lower() for t in meta["tags"].split(",") if t.strip()]
    title = meta.get("title", "").lower()
    found = []
    for name, slug, keys in TOPICS:
        hit = any(k == t or (len(k) > 3 and k in t) for k in keys for t in tags) or \
              (slug != "apple" and any(re.search(r"\b" + re.escape(k) + r"s?\b", title)
                                       for k in keys if k in TITLE_KEYS))
        if hit:
            found.append((name, slug))
    return found


def first_tag(meta):
    t = topics_for(meta)
    return t[0][0] if t else ""


def is_hands_on(meta):
    """Posts with `type: review` in the top section are Dave's own hands-on reviews."""
    return meta.get("type", "").strip().lower() in ("review", "hands-on", "hands-on review")


def hands_on_badge(meta):
    return '<span class="hands-on">&#10003; Hands-on review</span>' if is_hands_on(meta) else ""


def verdict_tag(meta):
    w = meta.get("verdict", "").strip().upper()
    if w not in ("BUY", "WAIT", "SKIP"):
        return ""
    return f'<span class="vtag v-{w.lower()}">{w}</span>'


def render_thumb(meta):
    """Image box with the category pill on top. Falls back to a themed placeholder."""
    src = post_image(meta)
    if src and src.startswith(IMG_MARK) and urllib.parse.unquote(src[len(IMG_MARK):]) in MISSING_IMAGES:
        src = None  # photo not uploaded (or name misspelled): show the placeholder, not a broken image
    tag = first_tag(meta)
    pill = f'<span class="pill">{html.escape(tag)}</span>' if tag else ""
    pill += verdict_tag(meta)
    if is_hands_on(meta):
        pill += '<span class="pill hands-on-pill">Hands-on</span>'
    if src:
        return (f'<span class="thumb">{pill}'
                f'<img src="{html.escape(fix_images(src, ""))}" alt="{html.escape(meta["title"])}" loading="lazy"></span>')
    return f'<span class="thumb ph">{pill}</span>'


def _modified(meta, published):
    """An "updated:" line in a post's frontmatter tells Google the post was refreshed."""
    raw = meta.get("updated", "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
            return (dt if dt.tzinfo else dt.replace(tzinfo=TIMEZONE)).isoformat(timespec="seconds")
        except ValueError:
            pass
    return published


def article_schema(meta, url):
    """NewsArticle structured data so Google reads the page as a dated news story."""
    base = SITE_URL.rstrip("/")
    published = meta["_published"].isoformat(timespec="seconds")
    data = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": meta["title"][:110],
        "description": meta.get("snippet", ""),
        "datePublished": published,
        "dateModified": _modified(meta, published),
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "author": [{"@type": "Person", "name": meta.get("author", AUTHOR_NAME), "url": f"{base}/about.html"}],
        "publisher": {"@type": "Organization", "name": SITE_TITLE, "url": base + "/",
                      "logo": {"@type": "ImageObject", "url": f"{base}/icon-512.png", "width": 512, "height": 512}},
    }
    img = post_image(meta)
    if img and not (img.startswith(IMG_MARK) and urllib.parse.unquote(img[len(IMG_MARK):]) in MISSING_IMAGES):
        data["image"] = [fix_images(img, base + "/")]
    if meta.get("tags"):
        data["keywords"] = [t.strip() for t in meta["tags"].split(",") if t.strip()]
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def render_byline(meta, latest=False):
    author = html.escape(meta.get("author", AUTHOR_NAME))
    live = '<span class="live">Latest</span>' if latest else ""
    return (f'<span class="byline">{live}<span>{author}</span>'
            f'<time>{fmt_date_human(meta["_date_obj"])}</time></span>')


def render_lead_story(meta):
    href = f"posts/{meta['slug']}.html"
    dek = f'<p class="dek">{html.escape(meta["snippet"])}</p>' if meta["snippet"] else ""
    return (f'<a class="story story-lead" href="{href}">{render_thumb(meta)}'
            f'<h2>{html.escape(meta["title"])}</h2>{render_byline(meta, latest=True)}{dek}</a>')


def render_card(meta, with_dek=False):
    href = f"posts/{meta['slug']}.html"
    dek = f'<p class="dek">{html.escape(meta["snippet"])}</p>' if (with_dek and meta["snippet"]) else ""
    return (f'<a class="story story-card" href="{href}">{render_thumb(meta)}'
            f'<h3>{html.escape(meta["title"])}</h3>{render_byline(meta)}{dek}</a>')


def render_top(posts):
    lead = posts[0]
    side = posts[1:1 + SIDE_COUNT]
    solo = " solo" if not side else ""
    side_html = ('<div class="top-side">' + "".join(render_card(m) for m in side) + '</div>') if side else ""
    return f'<section class="top{solo}">{render_lead_story(lead)}{side_html}</section>'


def render_home_body(posts):
    more = posts[1 + SIDE_COUNT:1 + SIDE_COUNT + MORE_COUNT]
    older = posts[1 + SIDE_COUNT + MORE_COUNT:]
    out = ['<div class="home-topics">' + render_topic_strip("") + '</div>']

    if more:
        out.append('<h2 class="sec-title">More stories</h2>')
        out.append('<div class="more">' + "".join(render_card(m, with_dek=True) for m in more) + '</div>')

    if older:
        out.append('<h2 class="sec-title">Older stories</h2>')
        items = "".join(render_feed_item(m, f"posts/{m['slug']}.html") for m in older)
        out.append(f'<ul class="feed older">{items}</ul>')

    return "\n".join(out)


def render_hero(posts):
    return f"""
<section class="hero" id="hero">
  <canvas id="ridge" aria-hidden="true"></canvas>
  <div class="wrap in">
    <h1 class="sr-only">{html.escape(SITE_TITLE)}</h1>
    <p class="hero-tag">{html.escape(HERO_SUBLINE)}</p>
    {render_top(posts)}
  </div>
</section>
"""


# ---------------------------------------------------------------------------
# Image speed-ups. If Pillow is installed (the deploy workflow installs it),
# each photo in images/ also gets two small WebP copies in dist/images/_w/:
# a 640px one for the homepage cards and a 1400px one for post pages.
# The originals stay untouched. Without Pillow the site still builds and just
# uses the originals.
# ---------------------------------------------------------------------------

THUMB_W = 640
LARGE_W = 1400
RESIZED = {}  # original file name -> {"thumb": "_w/x-640.webp", "large": "_w/x-1400.webp"}


def make_resized_images():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        print("NOTE: Pillow isn't installed, so images are served at full size.")
        return
    src_dir = IMAGES_DIR
    out_dir = os.path.join(DIST_DIR, "images", "_w")
    os.makedirs(out_dir, exist_ok=True)
    for name in sorted(os.listdir(src_dir)):
        path = os.path.join(src_dir, name)
        if not os.path.isfile(path) or not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".avif")):
            continue
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                stem = slugify(name) or "img"  # keeps the extension in, so x.jpg and x.png never clash
                out = {}
                for key, width in (("thumb", THUMB_W), ("large", LARGE_W)):
                    copy = im.copy()
                    if copy.width > width:
                        copy = copy.resize((width, round(copy.height * width / copy.width)), Image.LANCZOS)
                    rel = f"_w/{stem}-{width}.webp"
                    copy.save(os.path.join(DIST_DIR, "images", rel), "WEBP", quality=80, method=4)
                    out[key] = rel
                RESIZED[name] = out
        except Exception as e:  # a broken or unusual file just keeps its original
            print(f"NOTE: couldn't resize image '{name}' ({e}); using the original.")


_IMG_TAG_RE = re.compile(r"<img\b[^>]*>")
_IMG_SRC_RE = re.compile(r'src="((?:\.\./)?images/)([^"]+)"')


def speed_up_images(page_html, first_image_eager=False):
    """Point <img> tags at the small WebP copies and let the browser load them lazily."""
    count = [0]

    def fix(m):
        tag = m.group(0)
        count[0] += 1
        eager = first_image_eager and count[0] == 1
        sm = _IMG_SRC_RE.search(tag)
        if sm:
            name = urllib.parse.unquote(sm.group(2))
            sizes = RESIZED.get(name)
            if sizes:
                in_card = 'class="thumb' in page_html[max(0, m.start() - 120):m.start()]
                kind = "thumb" if in_card and not eager else "large"  # the big lead story gets the sharper copy
                tag = tag.replace(sm.group(0), f'src="{sm.group(1)}{sizes[kind]}"', 1)
        if "loading=" not in tag:
            tag = tag.replace("<img", '<img loading="eager" fetchpriority="high"' if eager else '<img loading="lazy"', 1)
        elif eager:
            tag = tag.replace('loading="lazy"', 'loading="eager" fetchpriority="high"', 1)
        if "decoding=" not in tag:
            tag = tag.replace("<img", '<img decoding="async"', 1)
        return tag

    return _IMG_TAG_RE.sub(fix, page_html)


def speed_up_all_pages():
    for folder in (DIST_DIR, os.path.join(DIST_DIR, "posts")):
        for fname in os.listdir(folder):
            if not fname.endswith(".html"):
                continue
            path = os.path.join(folder, fname)
            with open(path, encoding="utf-8") as f:
                page = f.read()
            # The first picture on a post page (or the lead story on the homepage) is
            # what people see first, so that one loads right away.
            fixed = speed_up_images(page, first_image_eager=True)
            if fixed != page:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(fixed)


VERDICT_WORDS = {"BUY": "buy", "WAIT": "wait", "SKIP": "skip"}
_AMAZON_LINK_RE = re.compile(r'<a href="(https://www\.amazon\.com/[^"]+)">(.*?)</a>')
_H2_RE = re.compile(r'<h2 id="([^"]+)">(.*?)</h2>')


def reading_minutes(body_md):
    words = len(re.findall(r"\w+", re.sub(r"\(https?://[^)]+\)", "", body_md)))
    return max(1, round(words / 230))


def amazon_pick(meta):
    """First Amazon product link in the post: (url, product name) or None."""
    m = _AMAZON_LINK_RE.search(meta["body_html"])
    return (m.group(1), _strip_tags(m.group(2))) if m else None


def render_price_button(pick, small=False):
    if not pick:
        return ""
    url, name = pick
    cls = "price-btn small" if small else "price-btn"
    return (f'<a class="{cls}" href="{html.escape(url)}" target="_blank" rel="sponsored noopener">'
            f'Check price on Amazon<span class="price-for">{html.escape(name)}</span></a>')


def render_verdict(meta, pick):
    word = meta.get("verdict", "").strip().upper()
    if word not in VERDICT_WORDS:
        return ""
    line = html.escape(meta.get("verdict_line", "").strip())
    pills = "".join(f'<span class="vpill{" on" if w == word else ""}">{w}</span>' for w in VERDICT_WORDS)
    return (f'<aside class="verdict v-{VERDICT_WORDS[word]}" aria-label="Our verdict">'
            f'<div class="v-head"><span class="v-label">Worth it or a waste?</span><span class="v-pills">{pills}</span></div>'
            f'<p class="v-line">{line}</p>{render_price_button(pick)}</aside>')


def render_toc(body_html, cls):
    heads = [(i, _strip_tags(t)) for i, t in _H2_RE.findall(body_html)]
    heads = [(i, t) for i, t in heads if t.lower() not in ("sources", "the bottom line")]
    if len(heads) < 3:
        return ""
    items = "".join(f'<li><a href="#{i}">{html.escape(t)}</a></li>' for i, t in heads)
    if cls == "toc-inline":
        return f'<details class="toc toc-inline"><summary>In this post</summary><ol>{items}</ol></details>'
    return f'<nav class="toc toc-side" aria-label="In this post"><p class="toc-title">In this post</p><ol>{items}</ol></nav>'


def _tagset(meta):
    tags = {t.strip().lower().replace(" ", "") for t in meta["tags"].split(",") if t.strip()}
    return tags | {"topic:" + slug for _, slug in topics_for(meta) if slug != "apple"}


def related_posts(meta, posts, k=3):
    mine = _tagset(meta)
    others = [p for p in posts if p is not meta]
    scored = sorted(others, key=lambda p: (len(mine & _tagset(p)), p["_published"]), reverse=True)
    return scored[:k]


def render_card_at(p, prefix, with_dek=False):
    """Story card for pages in a subfolder (posts/, topics/): prefix is "../"."""
    src = post_image(p)
    vt = verdict_tag(p)
    thumb = (f'<span class="thumb">{vt}<img src="{html.escape(src)}" alt="{html.escape(p["title"])}" loading="lazy"></span>'
             if src and not (src.startswith(IMG_MARK) and urllib.parse.unquote(src[len(IMG_MARK):]) in MISSING_IMAGES)
             else f'<span class="thumb ph">{vt}</span>')
    dek = f'<p class="dek">{html.escape(p["snippet"])}</p>' if (with_dek and p["snippet"]) else ""
    return (f'<a class="story story-card" href="{prefix}posts/{p["slug"]}.html">{thumb}'
            f'<h3>{html.escape(p["title"])}</h3>{render_byline(p)}{dek}</a>')


def render_related(meta, posts):
    cards = [render_card_at(p, "../") for p in related_posts(meta, posts)]
    if not cards:
        return ""
    return f'<section class="keep-reading"><h2>Keep reading</h2><div class="kr-grid">{"".join(cards)}</div></section>'


def render_author_box():
    photo = ""
    if os.path.isfile(os.path.join(IMAGES_DIR, ABOUT_PHOTO)):
        photo = (f'<img class="ab-photo" src="{IMG_MARK}{urllib.parse.quote(ABOUT_PHOTO)}" '
                 f'alt="{html.escape(AUTHOR_NAME)}" width="72" height="72" loading="lazy">')
    return (f'<section class="author-box">{photo}<div><p class="ab-name">Written by '
            f'<a href="../about.html" rel="author">{html.escape(AUTHOR_NAME)}</a></p>'
            f'<p class="ab-bio">Founder of {SITE_TITLE} in Toronto. Every post answers one question: is it worth it, '
            f'or a waste of money?</p><div class="soc">{render_social_icons()}</div></div></section>')


def render_topic_strip(prefix, current=""):
    by = {slug: name for name, slug, _ in TOPICS}
    links = "".join(f'<a href="{prefix}topics/{s}.html"{" aria-current=\"page\"" if s == current else ""}>{html.escape(by[s])}</a>'
                    for s in MENU_TOPICS)
    return f'<nav class="topic-strip" aria-label="Topics">{links}</nav>'


def build_search_index(posts):
    items = []
    for m in posts:
        items.append({"t": m["title"], "u": f"posts/{m['slug']}.html", "s": m["snippet"],
                      "d": fmt_date_human(m["_date_obj"]), "v": m.get("verdict", "").strip().upper(),
                      "k": " ".join([m["tags"]] + [n for n, _ in topics_for(m)])})
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def build():
    if os.path.isdir(DIST_DIR):
        shutil.rmtree(DIST_DIR)
    os.makedirs(os.path.join(DIST_DIR, "posts"), exist_ok=True)

    # Copy images/ into dist/images/ so they're served alongside the site
    if os.path.isdir(IMAGES_DIR):
        shutil.copytree(IMAGES_DIR, os.path.join(DIST_DIR, "images"))
        make_resized_images()

    # Favicon files go at the top level of the site (browsers look for /favicon.ico)
    if os.path.isdir(FAVICON_DIR):
        for name in os.listdir(FAVICON_DIR):
            src = os.path.join(FAVICON_DIR, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(DIST_DIR, name))

    # IndexNow key file (see INDEXNOW_KEY near the top)
    if INDEXNOW_KEY:
        with open(os.path.join(DIST_DIR, f"{INDEXNOW_KEY}.txt"), "w", encoding="utf-8") as f:
            f.write(INDEXNOW_KEY)

    # Copy social-cards/ into dist/ so each day's card is downloadable
    if os.path.isdir(SOCIAL_CARDS_DIR):
        shutil.copytree(SOCIAL_CARDS_DIR, os.path.join(DIST_DIR, "social-cards"))

    # Netlify cache headers (a plain file named "_headers" in the published folder)
    headers_content = """/images/*
  Cache-Control: public, max-age=2592000

/feed.xml
  Cache-Control: public, max-age=3600

/sitemap.xml
  Cache-Control: public, max-age=3600
"""
    with open(os.path.join(DIST_DIR, "_headers"), "w", encoding="utf-8") as f:
        f.write(headers_content)

    md_files = sorted(f for f in os.listdir(POSTS_DIR) if f.endswith(".md"))
    if not md_files:
        raise SystemExit("No .md files found in posts/ — nothing to build.")

    posts = []
    for fname in md_files:
        path = os.path.join(POSTS_DIR, fname)
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        meta, body = parse_frontmatter(raw, fname)
        body = strip_placeholders(body)
        meta["slug"] = meta.get("slug") or slugify(meta["title"])
        meta["body_html"] = markdown_to_html(body)
        meta["_body_md"] = body
        posts.append(meta)

    posts.sort(key=lambda m: m["_published"], reverse=True)

    with open(os.path.join(DIST_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(generate_rss(posts))
    with open(os.path.join(DIST_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(generate_sitemap(posts))
    with open(os.path.join(DIST_DIR, "news-sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(generate_news_sitemap(posts))
    with open(os.path.join(DIST_DIR, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(generate_robots_txt())

    # One Apple News Format article.json per post: dist/anf/<slug>/article.json
    for meta in posts:
        anf_dir = os.path.join(DIST_DIR, "anf", meta["slug"])
        os.makedirs(anf_dir, exist_ok=True)
        with open(os.path.join(anf_dir, "article.json"), "w", encoding="utf-8") as f:
            json.dump(generate_anf(meta), f, indent=2, ensure_ascii=False)

    base = SITE_URL.rstrip("/")

    # ---- individual post pages ----
    for meta in posts:
        hero_html = ""
        cover = meta.get("image", "").strip()
        if cover:
            src = resolve_image_src(cover)
            first = _BODY_IMG_RE.search(meta["body_html"])
            already_in_text = first and first.group(1) == src and meta["body_html"].lstrip().startswith("<p><img")
            missing = src.startswith(IMG_MARK) and urllib.parse.unquote(src[len(IMG_MARK):]) in MISSING_IMAGES
            if not already_in_text and not missing:
                hero_html = (f'<figure class="post-hero"><img src="{html.escape(src)}" '
                             f'alt="{html.escape(meta["title"])}"></figure>')
        topic_list = topics_for(meta)
        tags_html = ""
        if topic_list:
            links = "".join(f'<a href="../topics/{slug}.html">{html.escape(name)}</a>' for name, slug in topic_list)
            tags_html = f'<div class="tags-row"><span class="tags-label">More on</span>{links}</div>'

        share_img = post_image(meta)
        share_img = fix_images(share_img, base + "/") if share_img else ""
        share_html = render_share_bar(f"{base}/posts/{meta['slug']}.html", meta["title"], share_img)

        pick = amazon_pick(meta)
        verdict_html = render_verdict(meta, pick)
        # posts without a verdict still get the price button, after the article text
        late_price = "" if verdict_html or not pick else f'<div class="price-card">{render_price_button(pick)}</div>'
        side_verdict = ""
        if verdict_html:
            word = meta["verdict"].strip().upper()
            side_verdict = (f'<div class="side-verdict v-{VERDICT_WORDS[word]}"><span class="v-label">Our verdict</span>'
                            f'<b>{word}</b>{render_price_button(pick, small=True)}</div>')
        mins = reading_minutes(meta.get("_body_md", ""))
        updated_html = ""
        upd = meta.get("updated", "").strip()[:10]
        if upd and upd != meta["date"]:
            try:
                updated_html = (f' &middot; <span class="updated">Updated '
                                f'{fmt_date_human(datetime.strptime(upd, "%Y-%m-%d"))}</span>')
            except ValueError:
                pass
        body = f"""
    <div class="post-layout">
    <article class="post-full">
      <a class="back-link" href="../index.html">&#8592; back to index</a>
      <p class="post-meta">{hands_on_badge(meta)}By <a class="author-link" href="../about.html" rel="author">{html.escape(meta.get('author', AUTHOR_NAME))}</a>
        &middot; <time datetime="{meta['_published'].isoformat(timespec='seconds')}">{fmt_date_human(meta['_date_obj'])}</time>{updated_html}
        &middot; <span class="read-time">{mins} min read</span></p>
      <h1 class="post-title">{html.escape(meta['title'])}</h1>
      {verdict_html}
      {hero_html}
      {render_toc(meta['body_html'], 'toc-inline')}
      <div class="post-body">
        {meta['body_html']}
      </div>
      {late_price}
      {tags_html}
      {render_author_box()}
      {share_html}
      {render_related(meta, posts)}
    </article>
    <aside class="post-side">
      <div class="side-sticky">{side_verdict}{render_toc(meta['body_html'], 'toc-side')}</div>
    </aside>
    </div>
    <a class="to-top" href="#" aria-label="Back to top">&#8593;</a>"""
        page = render_page(
            title=f"{html.escape(meta['title'])} &middot; {SITE_TITLE}",
            description=html.escape(meta_description(meta["snippet"], meta.get("_body_md", ""))),
            canonical=f"{base}/posts/{meta['slug']}.html",
            prefix="../",
            body=body,
            image=post_image(meta),
            og_type="article",
            head_extra=(('<meta name="robots" content="noindex, follow"/>\n' if not in_google(meta) else "")
                        + article_schema(meta, f"{base}/posts/{meta['slug']}.html")
                        + f'\n<meta property="article:published_time" content="{meta["_published"].isoformat(timespec="seconds")}"/>'
                        + f'\n<meta property="article:author" content="{html.escape(meta.get("author", AUTHOR_NAME))}"/>'),
        )
        with open(os.path.join(DIST_DIR, "posts", f"{meta['slug']}.html"), "w", encoding="utf-8") as f:
            f.write(page)

    # ---- homepage: animated hero, then lead story + grid + more stories ----
    # Tells Google the site's name ("TechByDave" instead of "techbydave.com")
    # and logo for the search result header.
    site_schema = json.dumps({
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "@id": base + "/#website", "name": SITE_TITLE,
             "alternateName": ["Tech by Dave", "techbydave.com"], "url": base + "/",
             "publisher": {"@id": base + "/#org"}},
            {"@type": "Organization", "@id": base + "/#org", "name": SITE_TITLE, "url": base + "/",
             "logo": {"@type": "ImageObject", "url": f"{base}/icon-512.png", "width": 512, "height": 512},
             "sameAs": [u for _, u in SOCIAL_LINKS]},
        ],
    }, ensure_ascii=False).replace("</", "<\\/")
    index = render_page(
        title=f"{SITE_TITLE} | Is It Worth It? Honest Tech Reviews &amp; Deals",
        head_extra=f'<script type="application/ld+json">{site_schema}</script>',
        description=html.escape(f"{SITE_TITLE} gives honest buy, wait or skip verdicts on new phones, laptops, earbuds and "
                                f"smart home gear, plus deals and cheaper alternatives."),
        canonical=base + "/",
        body=render_home_body(posts) + '\n<div hidden>Impact-Site-Verification: 6a4a9247-21ee-4a85-be00-2e3d141b9103</div>',
        hero=render_hero(posts),
        main_class="home",
    )
    with open(os.path.join(DIST_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index)

    # ---- about, privacy, editorial policy, contact ----
    for filename, page_title, desc, md in (
        ("about.html", f"About {SITE_TITLE}",
         f"Meet {AUTHOR_NAME}, founder of {SITE_TITLE}: what the site covers, the mission to make the best tech content, "
         f"and the vision of a foundation that helps people in need.", ABOUT_MARKDOWN),
        ("privacy.html", "Privacy Policy",
         f"How {SITE_TITLE} handles your data: ads and cookies, privacy-friendly analytics, Amazon affiliate links, "
         f"and what happens to your newsletter email.", PRIVACY_MARKDOWN),
        ("editorial-policy.html", "Editorial Policy",
         f"How {SITE_TITLE} picks stories, sources every fact, uses AI-assisted drafts, discloses affiliate links "
         f"and ads, and corrects mistakes.", EDITORIAL_MARKDOWN),
        ("contact.html", "Contact",
         f"Contact {SITE_TITLE}: email {AUTHOR_NAME} with news tips, product review requests, corrections, "
         f"or advertising and partnership questions.", CONTACT_MARKDOWN),
        ("media-kit.html", "Media Kit",
         f"Work with {SITE_TITLE}: audience, product reviews, launch coverage, newsletter sponsorships "
         f"and how to send review units.", MEDIA_KIT_MARKDOWN),
    ):
        intro = ""
        if filename == "about.html" and os.path.isfile(os.path.join(IMAGES_DIR, ABOUT_PHOTO)):
            intro = (f'<div class="about-card"><img class="about-photo" src="{IMG_MARK}{urllib.parse.quote(ABOUT_PHOTO)}" '
                     f'alt="{html.escape(AUTHOR_NAME)}, founder of {SITE_TITLE}" width="160" height="160">'
                     f'<div><div class="about-name">{html.escape(AUTHOR_NAME)}</div>'
                     f'<div class="about-role">Founder &amp; writer, {SITE_TITLE}</div></div></div>')
        body = f"""
    <article class="post-full">
      <h1 class="post-title">{html.escape(page_title)}</h1>
      {intro}
      <div class="post-body">
        {markdown_to_html(md)}
      </div>
    </article>"""
        simple = render_page(
            title=f"{html.escape(page_title)} &middot; {SITE_TITLE}",
            description=html.escape(desc),
            canonical=f"{base}/{filename}",
            body=body,
        )
        with open(os.path.join(DIST_DIR, filename), "w", encoding="utf-8") as f:
            f.write(simple)

    # ---- search index, app manifest ----
    with open(os.path.join(DIST_DIR, "search.json"), "w", encoding="utf-8") as f:
        f.write(build_search_index(posts))
    manifest = {
        "name": SITE_TITLE, "short_name": SITE_TITLE, "description": SITE_TAGLINE,
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#070a14", "theme_color": "#070a14",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }
    with open(os.path.join(DIST_DIR, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # ---- topic pages: topics/<slug>.html ----
    os.makedirs(os.path.join(DIST_DIR, "topics"), exist_ok=True)
    topic_counts = {}
    for name, slug, _ in TOPICS:
        tp = [m for m in posts if slug in {s2 for _, s2 in topics_for(m)}]
        topic_counts[slug] = len(tp)
        if not tp:
            continue
        cards = "".join(render_card_at(m, "../", with_dek=True) for m in tp)
        body = f"""
    <header class="page-head">
      <h1>{html.escape(name)}</h1>
      <p>{len(tp)} post{"s" if len(tp) != 1 else ""} with honest buy, wait or skip verdicts on {html.escape(name.lower())}.</p>
      {render_topic_strip("../", slug)}
    </header>
    <div class="topic-grid">{cards}</div>"""
        page = render_page(
            title=f"{html.escape(name)}: Is It Worth It? &middot; {SITE_TITLE}",
            description=html.escape(f"Every {SITE_TITLE} post on {name.lower()}: honest buy, wait or skip verdicts, "
                                    f"deals and smarter alternatives."),
            canonical=f"{base}/topics/{slug}.html",
            prefix="../",
            body=body,
        )
        with open(os.path.join(DIST_DIR, "topics", f"{slug}.html"), "w", encoding="utf-8") as f:
            f.write(page)

    # ---- all posts, by month ----
    months = []
    for m in sorted(posts, key=lambda m: (m["_date_obj"], m["_published"]), reverse=True):
        key = m["_date_obj"].strftime("%B %Y")
        if not months or months[-1][0] != key:
            months.append((key, []))
        months[-1][1].append(m)
    arch = []
    for key, ms in months:
        rows = "".join(f'<li><time>{m["_date_obj"].strftime("%b %-d")}</time>'
                       f'<a href="posts/{m["slug"]}.html">{html.escape(m["title"])}</a>{verdict_tag(m)}</li>' for m in ms)
        arch.append(f'<h2 class="archive-month">{key}</h2><ul class="archive-list">{rows}</ul>')
    archive = render_page(
        title=f"All posts &middot; {SITE_TITLE}",
        description=html.escape(f"Every {SITE_TITLE} post, newest first, with its buy, wait or skip verdict."),
        canonical=f"{base}/archive.html",
        body=f"""
    <header class="page-head">
      <h1>All posts</h1>
      <p>{len(posts)} posts, newest first. Browse by topic:</p>
      {render_topic_strip("")}
    </header>
    {"".join(arch)}""",
    )
    with open(os.path.join(DIST_DIR, "archive.html"), "w", encoding="utf-8") as f:
        f.write(archive)

    # ---- 404 page (GitHub Pages shows this for any address that doesn't exist) ----
    # Links start with "/" so they work no matter how deep the bad address was.
    not_found = render_page(
        title=f"Page not found &middot; {SITE_TITLE}",
        description=html.escape(f"This page doesn't exist on {SITE_TITLE}."),
        canonical=f"{base}/404.html",
        prefix="/",
        body=f"""
    <article class="post-full">
      <h1 class="post-title">That page isn't here</h1>
      <div class="post-body">
        <p>The link may be old, or the address may have a typo. Try the <a href="/index.html">latest stories</a>
        on the homepage, or <a href="/contact.html">let {html.escape(AUTHOR_NAME)} know</a> if something's broken.</p>
      </div>
    </article>""",
        head_extra='<meta name="robots" content="noindex"/>',
    )
    with open(os.path.join(DIST_DIR, "404.html"), "w", encoding="utf-8") as f:
        f.write(not_found)

    speed_up_all_pages()

    shown = sum(1 for m in posts if in_google(m))
    print(f"Google: {shown} post(s) shown in search, {len(posts) - shown} hidden (no 'My take' section yet).")

    for name in sorted(MISSING_IMAGES):
        print(f"WARNING: image '{name}' is used in a post but is not in the images/ folder. "
              "Check the spelling (capitals count) and that it was uploaded.")
    print(f"Built {len(posts)} post(s) into {DIST_DIR}/")


if __name__ == "__main__":
    build()
