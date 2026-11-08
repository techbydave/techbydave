# Tech Trek daily auto-poster

Runs every day on GitHub's own servers (not your machine), writes 3 AI-researched
tech news posts into `posts/`, rebuilds the site with your existing `build.py`,
and pushes. Netlify (already connected to your GitHub repo) picks up the push
and redeploys automatically. You don't touch anything after setup.

## Setup (one-time, ~5 minutes)

1. **Copy these files into your Tech Trek repo**, keeping the same paths:
   - `.github/workflows/daily-blog-post.yml`
   - `scripts/generate_posts.py`
 
2. **Get an Anthropic API key**: console.anthropic.com → API Keys → Create Key.
   (This uses the paid API, billed per token — 3 posts/day is roughly a few
   cents to ~$0.10/day depending on length and how much search it does.
   Check current pricing at anthropic.com/pricing.)
 
3. **Add it as a GitHub secret**: in your repo, go to
   Settings → Secrets and variables → Actions → New repository secret.
   - Name: `ANTHROPIC_API_KEY`
   - Value: the key from step 2

4. **Push the two files to `main`** (or your default branch). That's it — the
   workflow will run automatically on the schedule in the `.yml` file, and you
   can also trigger it manually anytime from the repo's **Actions** tab →
   "Daily Tech Trek posts" → "Run workflow" (good for testing before you wait
   for the schedule).

## Before you rely on it: fix the frontmatter format

I don't have your `build.py`, so `generate_posts.py` currently writes
frontmatter as:

```
---
title: "..."
date: 2026-09-09
tags: ["ai", "hardware"]
summary: "..."
---
```

Open `build.py`, find where it parses each post file, and check the field
names it actually looks for. If they don't match, edit the
`FRONTMATTER_TEMPLATE` variable and the JSON keys requested in the prompt
inside `generate_posts.py` to match exactly — otherwise the files will land
in `posts/` but won't render on the live site. Paste me `build.py` and I can
fix this exactly instead of guessing.

## Notes / things worth knowing

- **Topic dedup**: the script reads your last ~40 post titles and tells
  Claude not to repeat them, so it won't write the same story twice.
- **Web search**: research happens via Claude's built-in web search tool —
  no separate news API needed.
- **Schedule**: GitHub Actions cron times aren't perfectly precise (can run a
  few minutes late) and use UTC — the workflow file has a comment showing the
  Toronto-local equivalent.
- **Cost control**: if you want a ceiling, you can add a monthly spend limit
  in the Anthropic console.
- **Silent failures**: if something breaks (bad API key, JSON parsing
  failure, etc.), the Action run will show as failed in the Actions tab —
  worth glancing at occasionally, or add a "GitHub Actions failure" email/
  Slack notification in your GitHub notification settings so you find out
  without checking manually.
- **Which posts Google shows**: a post only appears in Google search once it
  has a `## My take` section with your own view. Posts without one stay on
  the site for readers, but get a "noindex" tag and are left out of the
  sitemaps. Add a take to any post and it goes back into Google on the next
  deploy. To override one post, add `google: yes` (always show) or
  `google: no` (always hide) to its frontmatter.
