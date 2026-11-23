#!/usr/bin/env python3
"""
Tuesday and Thursday news drafts for TechByDave.

At 5:00am Toronto time on Tuesdays and Thursdays this:
  1. uses Claude + web search to find the TWO biggest consumer-tech stories of
     the week (launches, openings, price changes, big announcements),
  2. writes each one into drafts/ as a QUESTION-AND-ANSWER draft, like the
     iPhone 15 Plus draft: a search-friendly headline, tags, a verdict, and a
     list of questions for YOU to answer in your own words. Under each
     question are the facts the research found, so you don't have to dig.
  3. puts the sources at the bottom of the post.

Nothing goes live on its own. Every question is an "[[ADD YOUR ANSWER: ...]]"
line, and the 9:00am publish step (publish_drafts.py) holds a draft until all
of those lines are replaced with your answers.

The Monday/Wednesday/Friday buying-guide drafts are a separate script
(generate_posts.py) and keep running as before.

Run by .github/workflows/news-drafts.yml. Needs ANTHROPIC_API_KEY.
"""

import os
import re
import sys
from pathlib import Path

import anthropic

from generate_posts import (
    DRAFTS_DIR,
    MODEL,
    AMAZON_AFFILIATE_TAG,
    _amazon_search_url,
    _clean_sources,
    _single_line,
    _strip_citation_tags,
    _tag_list,
    AFFILIATE_DISCLOSURE,
    existing_titles,
    now_local,
    slugify,
)

NEWS_DAYS = {1, 3}      # Tuesday, Thursday (Monday=0)
NUM_STORIES = 2
MAX_SEARCHES = 8        # 2 to find the week's biggest stories, then ~3 per story

SUBMIT_TOOL = {
    "name": "submit_news_drafts",
    "description": "Submit both story drafts once research is done. Call exactly once, as your final action.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stories": {
                "type": "array",
                "description": f"Exactly {NUM_STORIES} different stories.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": (
                            "The headline. Under 60 characters, one line, written the way people type it into "
                            "Google: exact product or company name first, then the key fact or question "
                            "(e.g. 'Galaxy S27 Release Date and Price: What We Know'). No puns.")},
                        "snippet": {"type": "string", "description": "Meta description: one sentence under 155 characters that answers the headline."},
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "3-5 short lowercase tags (brand, product type, 'news')."},
                        "verdict": {"type": "string", "enum": ["BUY", "WAIT", "SKIP"], "description": "For a reader thinking of buying the product in the story: buy now, wait, or skip."},
                        "verdict_line": {"type": "string", "description": "One sentence under 140 characters: who should buy, wait or skip, and why."},
                        "what_happened": {"type": "string", "description": "2-3 plain sentences of the core news with the key numbers (price, date, specs). Facts only."},
                        "questions": {
                            "type": "array",
                            "description": "5-7 questions readers actually search about this story, in a sensible reading order.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "heading": {"type": "string", "description": "The question as a '##' heading, phrased like a Google search (e.g. 'How much does the Pixel 11a cost in Canada?')."},
                                    "prompt": {"type": "string", "description": "One line telling Dave what to cover in his answer, e.g. 'Say whether you'd pay this and what you'd buy instead.'"},
                                    "facts": {"type": "array", "items": {"type": "string"}, "description": "2-5 short sourced facts he can use to answer. Each names its source in brackets, e.g. '(The Verge)'."},
                                },
                                "required": ["heading", "prompt", "facts"],
                            },
                        },
                        "product_mentions": {"type": "array", "items": {"type": "string"}, "description": "Exact names of real, buyable products in the facts. Empty if none."},
                        "sources": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}, "url": {"type": "string"}},
                                "required": ["name", "url"],
                            },
                            "description": "Every page the facts came from: site name and full https URL. At least 3, from at least 2 different outlets.",
                        },
                    },
                    "required": ["title", "snippet", "tags", "verdict", "verdict_line", "what_happened", "questions", "product_mentions", "sources"],
                },
            }
        },
        "required": ["stories"],
    },
}


def build_prompt(today, avoid_titles):
    avoid = ""
    if avoid_titles:
        avoid = "The blog already covered these, so pick different stories:\n" + "\n".join(f"- {t}" for t in avoid_titles[-30:]) + "\n\n"
    return f"""You are the research assistant for "TechByDave", a consumer tech blog whose angle is
"Worth it or a waste? Honest gadget verdicts and smarter buys." Today is {today:%A, %B %d, %Y}.

Your job: find the {NUM_STORIES} BIGGEST consumer-tech stories of the past 7 days and prepare a
question-and-answer draft for each. Dave (the editor) writes every answer himself; you supply the
headline, tags, verdict, the questions and the sourced facts he needs.

What counts as big: major launches and openings (a new phone, laptop, console, headset, wearable or
service going on sale or opening pre-orders), confirmed prices and release dates, big price drops,
and announcements people will search for this week. Stick to things readers can buy or will soon be
able to buy. Skip enterprise software, funding rounds, lawsuits and research papers unless they change
what a shopper should do. Pick two DIFFERENT companies or product types.

{avoid}You have {MAX_SEARCHES} web searches. Use the first two to find this week's biggest stories
(for example "biggest tech news this week" and "new gadget launch this week"), then about three per
story for the exact facts: price (US first; Canadian price only if a source states it), release or
opening date, key specs, what changed from the last model, and the best cheaper alternative.

Rules:
- Only state facts your search results confirm. If something is rumoured, say "rumoured" and name who reported it.
- Every fact names its source in brackets.
- The headline is for Google: exact product name first, the key fact or question after, under 60 characters.
- Questions are what real people search: price, release date, "is it worth it", "should I upgrade from X",
  "X vs Y", Canada availability, what's new. 5-7 per story.
- Never write as if you used the product. Never invent quotes.

Research first, then call submit_news_drafts exactly once."""


def call_claude(prompt):
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=12000,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}, SUBMIT_TOOL],
        messages=[{"role": "user", "content": prompt}],
    )
    call = next((b for b in response.content if b.type == "tool_use" and b.name == "submit_news_drafts"), None)
    if call is None:
        raise RuntimeError(f"Claude didn't call submit_news_drafts (stop_reason={response.stop_reason!r}).")
    stories = call.input.get("stories") or []
    if not stories:
        raise ValueError("No stories returned.")
    return stories[:NUM_STORIES]


def clean(text):
    return _single_line(_strip_citation_tags(str(text or "")))


def render_story(story, today):
    title = clean(story["title"])
    slug = slugify(title)
    if not slug:
        raise ValueError("Empty headline.")
    sources = _clean_sources(story.get("sources"))
    if not sources:
        raise ValueError(f"'{title}' has no sources.")

    parts = [
        "[[ADD YOUR HOOK: 2-3 sentences in your own words. Open with the most interesting fact, then say "
        "what you think in one line. What happened: " + clean(story.get("what_happened")) + "]]",
        "[[ADD YOUR PHOTO: an official product image (from the maker's press page) or your own photo or "
        "screenshot, saved in images/. Write it as ![What it shows](../images/your-file.jpg). Delete this "
        "line if you have none.]]",
    ]
    for q in story.get("questions", []):
        heading = clean(q.get("heading")).lstrip("#").strip()
        if not heading:
            continue
        facts = "\n".join(f"- {clean(f)}" for f in q.get("facts", []) if clean(f))
        parts.append(f"## {heading}")
        parts.append(f"[[ADD YOUR ANSWER: {clean(q.get('prompt'))}\nFacts you can use:\n{facts}]]")

    parts.append("## My take")
    parts.append("[[ADD YOUR TAKE: 2-3 sentences. Is it worth it or a waste, and who should buy, wait or skip? "
                 "Change the verdict at the top of this file if you disagree with it. Delete this whole line when done.]]")

    body = "\n\n".join(parts)
    names = [clean(n) for n in story.get("product_mentions", []) if clean(n)][:3]
    linked = bool(names and AMAZON_AFFILIATE_TAG)
    if linked:
        body += "\n\n**Check today's price:** " + ", ".join(f"[{n}]({_amazon_search_url(n)})" for n in names)
    body += "\n\n## Sources\n\n" + "\n".join(f"- [{n}]({u})" for n, u in sources)
    if linked:
        body += AFFILIATE_DISCLOSURE

    verdict = clean(story.get("verdict")).upper()
    if verdict not in ("BUY", "WAIT", "SKIP"):
        verdict = "WAIT"
    front = "\n".join([
        "---",
        f"title: {title}",
        f"date: {today.isoformat()}",
        f"slug: {slug}",
        "type: news",
        f"tags: {', '.join(_tag_list(story.get('tags')))}",
        f"snippet: {clean(story.get('snippet'))}",
        f"verdict: {verdict}",
        f"verdict_line: {clean(story.get('verdict_line'))}",
        "---",
    ])
    return slug, front + "\n\n" + body + "\n"


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)
    today = now_local().date()
    force = os.environ.get("FORCE", "").lower() == "true"
    if not force and today.weekday() not in NEWS_DAYS:
        print("No news drafts today: they're written on Tuesday and Thursday.")
        return
    if not force and any("type: news" in f.read_text(errors="ignore") for f in Path(DRAFTS_DIR).glob(f"{today.isoformat()}-*.md")):
        print("Today's news drafts already exist. Skipping.")
        return

    stories = call_claude(build_prompt(today, existing_titles()))
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for story in stories:
        try:
            slug, text = render_story(story, today)
        except ValueError as e:
            print(f"  skipped a story: {e}", file=sys.stderr)
            continue
        path = DRAFTS_DIR / f"{today.isoformat()}-{slug}.md"
        path.write_text(text, encoding="utf-8")
        written += 1
        print(f"  wrote {path}")
    if not written:
        raise RuntimeError("No drafts were written.")
    print(f"Done: {written} news draft(s).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"News draft generation failed: {e}", file=sys.stderr)
        sys.exit(1)
