#!/usr/bin/env python3
"""
Daily auto-poster for TechByDave.

Once a day (triggered by the GitHub Actions schedule) this:
  1. uses Claude + web search to find ONE trending consumer-tech story,
  2. writes the blog post (with Amazon affiliate links and a Sources line)
     into drafts/ for YOU to edit. A separate 9:00am step (publish_drafts.py)
     moves edited drafts into posts/, where the site picks them up,
  3. writes 4 ready-to-post tweets into tweets/ (a separate file).

Run by .github/workflows/deploy.yml. Needs ANTHROPIC_API_KEY (GitHub secret).
Optional repo variables: AMAZON_TAG, AMAZON_DOMAIN, SITE_URL.

Safety checks: it skips if a post for today already exists, and it will NOT
publish a post that is too short, too long, or has no source links.
The time of day is controlled by the cron lines in the workflow, not here.
"""

import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import anthropic

# ---- Config ----------------------------------------------------------------

POSTS_DIR = Path("posts")
TWEETS_DIR = Path("tweets")
DRAFTS_DIR = Path("drafts")  # new posts land here until you edit and publish them
TIMEZONE = "America/Toronto"

# Sonnet writes more naturally than Haiku but costs more per post.
# To go back to the cheaper model, use "claude-haiku-4-5-20251001".
MODEL = "claude-sonnet-5-5"
MAX_SEARCHES = 2  # 1 to see what's trending, 1 to confirm the story's facts.
                  # Each search costs about $0.01 plus its result tokens.
TAGLINE = "field notes on Tech"

# OPTIONAL: paste one of your best posts here (about 200-300 words, plain text).
# The model will match its rhythm and attitude. Leave it empty ("") to skip.
STYLE_SAMPLE = ""

SITE_URL = (os.environ.get("SITE_URL", "").strip() or "https://techbydave.com").rstrip("/")
AMAZON_AFFILIATE_TAG = os.environ.get("AMAZON_TAG", "").strip() or "techtreck02-20"
AMAZON_DOMAIN = os.environ.get("AMAZON_DOMAIN", "").strip() or "amazon.com"

MIN_WORDS, MAX_WORDS = 250, 700
NUM_TWEETS = 4
TWEET_LIMIT = 280
URL_COST = 25  # "\n\n" + 23 characters (X counts every link as 23)

# Added to every draft. The 9:00am publish step will NOT publish a draft that
# still contains this line, so a post only goes live after you have written
# your own take.
EDIT_MARKER_BLOCK = (
    "\n\n## My take\n\n"
    "[[ADD YOUR TAKE: 2-3 sentences on what you think, who should buy or skip, "
    "and anything you have actually tried. Delete this whole line when done.]]"
)

AFFILIATE_DISCLOSURE = (
    "\n\n*As an Amazon Associate I earn from qualifying purchases. TechByDave "
    "is a participant in the Amazon Services LLC Associates Program. Some "
    "links in this post may be affiliate links; if you buy something through "
    "them, we may earn a small commission at no extra cost to you.*"
)

# Matches build.py's parser: no quotes around values, plain comma-separated
# tags, "snippet" for the preview, and an explicit slug so tweet links match.
FRONTMATTER_TEMPLATE = """---
title: {title}
date: {date}
slug: {slug}
tags: {tags}
snippet: {snippet}
---

{body}
"""

SUBMIT_POST_TOOL = {
    "name": "submit_post",
    "description": (
        "Submit the finished blog post and tweets once research and writing "
        "are complete. Call this exactly once, as your final action."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Under 60 characters, single line, phrased the way people search: exact product name first, then the question or key fact."},
            "snippet": {"type": "string", "description": "One sentence, under 160 chars, single line."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 short lowercase tag strings.",
            },
            "body": {"type": "string", "description": "The full Markdown body of the post."},
            "product_mentions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Exact names of real, currently-purchasable products named in the "
                    "body, written character-for-character as in the body. Empty if none."
                ),
            },
            "sources": {
                "type": "array",
                "description": "The pages the post's facts came from.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Site name, e.g. 'The Verge'."},
                        "url": {"type": "string", "description": "Full https URL."},
                    },
                    "required": ["name", "url"],
                },
            },
            "tweets": {
                "type": "array",
                "description": (
                    f"Exactly {NUM_TWEETS} different tweets about the post. Each under 230 "
                    "characters, NO links (a link is added later), 0-2 hashtags, and only "
                    "facts that appear in the post."
                ),
                "items": {"type": "string"},
            },
        },
        "required": ["title", "snippet", "tags", "body", "product_mentions", "sources", "tweets"],
    },
}


def now_local():
    return datetime.now(ZoneInfo(TIMEZONE))


def slugify(text: str) -> str:
    # Same rules as build.py's slugify, so links always match.
    return re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")


def post_exists_for(day) -> bool:
    return any(d.exists() and any(d.glob(f"{day.isoformat()}-*.md")) for d in (POSTS_DIR, DRAFTS_DIR))


def existing_titles() -> list[str]:
    titles = []
    files = []
    for d in (POSTS_DIR, DRAFTS_DIR):
        if d.exists():
            files += list(d.glob("*.md"))
    for f in sorted(files, key=lambda p: p.name)[-45:]:
        text = f.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r'^title:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
        if m:
            titles.append(m.group(1))
    return titles


def build_prompt(avoid_titles: list[str]) -> str:
    avoid_block = ""
    if avoid_titles:
        avoid_block = (
            "Do NOT repeat these topics, the blog already covered them:\n"
            + "\n".join(f"- {t}" for t in avoid_titles[-30:])
            + "\n\n"
        )
    style_block = ""
    if STYLE_SAMPLE.strip():
        style_block = (
            "Here is a sample of the voice to match. Match its rhythm and "
            "attitude, not its topic or wording:\n---\n"
            + STYLE_SAMPLE.strip()
            + "\n---\n\n"
        )
    return f"""You write for "TechByDave" (tagline: "{TAGLINE}"), a consumer tech and
gadgets blog.

Voice: write like a sharp editor telling a friend what actually matters about
this story. Plain words, contractions, a mix of short and long sentences, and
one clear opinion (who should buy this, who should skip it, what is overhyped)
backed by the facts you found. A dry joke is fine; forced enthusiasm is not.
Avoid: opening with a question, "Here's the thing", "It's not just X, it's Y",
a three-item list in every paragraph, heavy em-dash use, "dive into",
"landscape", "game-changer", "in today's fast-paced digital world", and any
wrap-up paragraph that repeats the post. End on something practical, like what
to do or what to watch for.

{style_block}Beat: CONSUMER TECH AND GADGETS people actually buy: phones, laptops,
wearables, headphones, smart home, gaming hardware, cameras. Good topics are
new launches, notable price drops or deals, spec comparisons, and buying
advice. Avoid enterprise software, corporate finance, and research-paper
stories unless they tie directly to a product people can buy.

You have {MAX_SEARCHES} web searches in total:
1. Spend the first on what is trending in consumer tech right now (a broad
   query such as "trending gadget tech news today").
2. Spend the second on the exact facts (model name, price, specs, dates) of
   the ONE story you pick that has not been covered yet.
Do not search again after that.

{avoid_block}Write one post:
- 350-500 words of Markdown. No title heading inside the body (the title is
  in frontmatter). No "---" on its own line. No citation markup, footnote
  markers or <cite> tags.
- State only facts found in your search results. If a price or spec is not
  confirmed by a source, leave it out or say it is not confirmed yet.
- Write in an editorial voice. NEVER claim you tested, used, or owned a
  product, and never invent quotes or personal anecdotes.
- Write the title the way someone would type it into Google: the exact
  product name first, then the question people are asking or the key fact
  (for example "iPhone 16 Plus Discontinued: Why Apple Dropped It" or
  "Galaxy S26 Ultra Price in Canada and Release Date"). Under 60 characters,
  no clever wordplay. Answer that question in the first two sentences.
- Vary the structure from day to day; open with the most interesting specific
  thing, not a throat-clear.
- Name each real, purchasable product exactly the same way in the body and in
  product_mentions (for example "Sony WH-1000XM6", not "the new headphones").
  Re-read your body for named products before finishing.
- In sources, list the pages your facts came from (site name and full URL).

Also write {NUM_TWEETS} different tweets that promote the post: for example a
sharp take, a key number or fact, a question, a buying tip. Each under 230
characters, no links, at most 2 hashtags, and no hype or invented claims.

Research first, then call submit_post exactly once with everything. That is
how you deliver your answer, not as a message.
"""


def _strip_citation_tags(text: str) -> str:
    text = re.sub(r"<cite[^>]*>", "", text)
    return re.sub(r"</cite>", "", text)


def call_claude(prompt: str) -> dict:
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4500,
        tools=[
            {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES},
            SUBMIT_POST_TOOL,
        ],
        messages=[{"role": "user", "content": prompt}],
    )
    tool_call = next(
        (b for b in response.content if b.type == "tool_use" and b.name == "submit_post"),
        None,
    )
    if tool_call is None:
        text = "".join(b.text for b in response.content if b.type == "text")
        raise RuntimeError(
            "Claude finished without calling submit_post. "
            f"stop_reason={response.stop_reason!r}. Text output was:\n{text}"
        )
    post = tool_call.input
    for key in ("title", "snippet", "body"):
        if not isinstance(post.get(key), str) or not post[key].strip():
            raise ValueError(f"submit_post is missing '{key}'")
    return post


def _amazon_search_url(product_name: str) -> str:
    url = f"https://www.{AMAZON_DOMAIN}/s?k={quote_plus(product_name)}"
    if AMAZON_AFFILIATE_TAG:
        url += f"&tag={quote_plus(AMAZON_AFFILIATE_TAG)}"
    return url


def _add_affiliate_links(body: str, product_mentions):
    """Link the first mention of each named product to an Amazon search.
    Returns (body, whether_any_link_was_added)."""
    if not AMAZON_AFFILIATE_TAG or not product_mentions:
        return body, False
    linked_any = False
    for product in product_mentions:
        product = str(product).strip()
        if not product or f"]({_amazon_search_url(product)})" in body:
            continue
        pattern = re.compile(re.escape(product))
        if pattern.search(body):
            body = pattern.sub(
                lambda m: f"[{m.group(0)}]({_amazon_search_url(product)})", body, count=1
            )
            linked_any = True
    return body, linked_any


def _clean_sources(sources):
    out = []
    for s in sources or []:
        url = str(s.get("url", "")).strip()
        name = re.sub(r"[\[\]]", "", " ".join(str(s.get("name", "source")).split()))
        if url.startswith("https://") or url.startswith("http://"):
            out.append((name or "source", url))
    return out


def _single_line(text: str) -> str:
    return " ".join(text.split())


def make_tweets(raw_tweets, post_url):
    """Trim each tweet to fit X's 280 limit; add the post link to the first two."""
    tweets = []
    for i, t in enumerate(raw_tweets or []):
        text = _single_line(_strip_citation_tags(str(t)))
        if not text:
            continue
        with_link = i < 2
        room = TWEET_LIMIT - (URL_COST if with_link else 0)
        if len(text) > room:
            text = text[: room - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        final = f"{text}\n\n{post_url}" if with_link else text
        used = len(text) + (URL_COST if with_link else 0)
        tweets.append((final, used))
    return tweets


def write_tweets_file(tweets, today, title, post_url) -> Path:
    TWEETS_DIR.mkdir(parents=True, exist_ok=True)
    path = TWEETS_DIR / f"{today.isoformat()}.md"
    lines = [f"# Tweets for {today.isoformat()}", "", f"Post: [{title}]({post_url})", "",
             "Copy one into X. The count shown is how X will count it (links = 23).", ""]
    for n, (text, used) in enumerate(tweets, 1):
        lines += [f"### Tweet {n} ({used}/{TWEET_LIMIT})", "", "```text", text, "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_post(post: dict, today):
    title = _single_line(_strip_citation_tags(post["title"]))
    slug = slugify(title)
    if not slug:
        raise ValueError("Title produced an empty slug.")

    body = _strip_citation_tags(post["body"])
    body = re.sub(r"(?m)^\s*---+\s*$", "", body).strip()

    words = len(body.split())
    sources = _clean_sources(post.get("sources"))
    if not (MIN_WORDS <= words <= MAX_WORDS):
        raise ValueError(f"Post has {words} words (allowed {MIN_WORDS}-{MAX_WORDS}). Not publishing.")
    if not sources:
        raise ValueError("Post has no source links. Not publishing.")

    body, linked_any = _add_affiliate_links(body, post.get("product_mentions", []))
    body += EDIT_MARKER_BLOCK
    body += "\n\n**Sources:** " + ", ".join(f"[{n}]({u})" for n, u in sources)
    if linked_any:
        body += AFFILIATE_DISCLOSURE

    tags = ", ".join(t.strip() for t in post.get("tags", []) if t.strip())
    content = FRONTMATTER_TEMPLATE.format(
        title=title,
        date=today.isoformat(),
        slug=slug,
        tags=tags,
        snippet=_single_line(_strip_citation_tags(post.get("snippet", ""))),
        body=body,
    )
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFTS_DIR / f"{today.isoformat()}-{slug}.md"
    path.write_text(content, encoding="utf-8")
    return path, title, slug


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    now = now_local()
    today = now.date()
    force = os.environ.get("FORCE", "").lower() == "true"

    # The workflow's cron decides WHEN this runs. The only guard here is
    # "don't write a second post for the same day" (two crons fire daily).
    if not force and post_exists_for(today):
        print("A post for today already exists. Skipping.")
        return

    print(f"[{now.isoformat()}] Writing today's post...")
    post = call_claude(build_prompt(existing_titles()))

    path, title, slug = write_post(post, today)
    print(f"  wrote {path}")
    print(f"  product_mentions: {post.get('product_mentions', [])!r}")

    post_url = f"{SITE_URL}/posts/{slug}.html"
    tweets = make_tweets(post.get("tweets"), post_url)
    if tweets:
        print(f"  wrote {write_tweets_file(tweets, today, title, post_url)}")
    else:
        print("  WARNING: no tweets were returned.", file=sys.stderr)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Post generation failed: {e}", file=sys.stderr)
        sys.exit(1)
