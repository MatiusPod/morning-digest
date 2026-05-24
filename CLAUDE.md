# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A zero-infra daily AI news digest. A GitHub Actions cron calls Anthropic's API with the `web_search_20250305` tool to fetch news per topic, writes `digest.json`, commits it back to `main`, and a static GitHub Pages site (`index.html`) renders it. Topics, sources, and schedule are user-editable from the same page via the GitHub Contents API + a fine-grained PAT stored in the browser's `localStorage`.

There is no build step, no server, no package.json. Three files do everything: `config.json`, `fetch_news.py`, `index.html`. CI lives in `.github/workflows/nightly.yml`.

## Common commands

```bash
# Run the fetch script locally (writes digest.json)
ANTHROPIC_API_KEY=sk-ant-... python3 fetch_news.py

# Compile-check after editing fetch_news.py
python3 -m py_compile fetch_news.py

# Preview the site locally
python3 -m http.server 8000
# then open http://localhost:8000

# Trigger a real run without waiting for cron: Actions tab → "Nightly Digest" → Run workflow
# (workflow_dispatch bypasses the time gate)
```

No tests, no linter, no formatter — don't invent commands for them.

## Architecture

### Single source of truth: `config.json`

```json
{ "topics": [...], "sources": [...], "schedule": { "hour": 8, "timezone": "Europe/Rome" } }
```

Both `fetch_news.py` and the in-browser editor read and write this file. When changing the schema, update all three: `config.json`, the `loadConfig()`/`saveConfig()` flow in `index.html`, and the time-gate step in `nightly.yml`.

### `fetch_news.py` — per-topic structured fetch

One Anthropic `messages.create` call per topic. Tools: `web_search_20250305` (with `allowed_domains` = `config.sources`, `max_uses: 2`) plus a `submit_digest` structured tool that returns `{ executive_summary, stories[3..5] }`. The model is told to call `submit_digest` exactly once after searching.

Two failure modes are handled in-script and must stay handled:

- **`RateLimitError`** → exponential backoff (60s × attempt), up to 4 attempts.
- **`BadRequestError` with "domains not accessible to our user agent"** → Anthropic's crawler is blocked by some publishers' robots.txt (e.g. reuters, theverge, arstechnica as of this writing). `_parse_blocked_domains()` regex-extracts the offenders from the error string, `_strip_blocked()` removes them from the `web_search` tool, and `fetch_topic` retries. The pruned tools list is **returned to `main()`** so subsequent topics in the same run don't re-trip the same 400. If you refactor `fetch_topic`, preserve the `(result, tools)` return contract.

Output `digest.json` shape:
```json
{ "generated_at": "YYYY-MM-DD HH:MM UTC", "topics": [{ "title", "executive_summary", "stories": [...] }] }
```

`generated_at` doubles as the time-gate's "last successful run" sentinel — don't rename it without updating `nightly.yml`.

### `nightly.yml` — hourly cron + Python time gate

Cron is `'0 * * * *'` (every hour). The first step runs an inline Python time-gate that decides whether to actually execute the rest. **Why hourly + gate instead of one daily cron:** GitHub Actions scheduled workflows are routinely delayed 10–30+ minutes. A strict `now.hour == target_hour` check silently misses the day whenever cron drifts past the hour boundary. The gate logic:

- `workflow_dispatch` → always run (manual override for testing).
- Otherwise: parse `digest.json.generated_at` as UTC, convert to `config.schedule.timezone`, take the date. Run iff `now.hour >= target_hour` AND that date != today's local date. This catches up after drift but fires at most once per local day.

All downstream steps (`Set up Python`, install, run, commit) are gated by `if: steps.gate.outputs.should_run == 'true'`. If you add steps, gate them the same way.

### `index.html` — static SPA + in-browser config editor

Single file, no framework. Three "screens" (`show-login` / `show-home` / `show-news`) toggled by a body class. Renders `digest.json` at page load (fetched relatively).

Config editing flow: user pastes a **fine-grained PAT** (scope: `Contents: Read and write` on this repo only) → stored in `localStorage` → `gh()` helper PUTs `config.json` via the GitHub Contents API (base64 + SHA). The cron picks up the new config on its next tick. The PAT never leaves the browser.

Theming: anime-fantasy aesthetic (Frieren/Re:Zero). Fonts via Google Fonts CDN — Cinzel/Cinzel Decorative for headers, Lora for news body, Inter for UI chrome. The `ACCENTS` array drives per-topic-card color rotation. If you redesign, keep news body text in a legible serif (Lora or similar) — earlier pixel-font version was explicitly rejected as unreadable.

## Gotchas

- **Don't add `sources` that block Anthropic's crawler.** Bloomberg/TechCrunch/Axios/X currently work; Reuters/TheVerge/ArsTechnica don't. The auto-prune handles it at runtime, but you waste an API call per blocked domain on first hit. Check robots.txt for `ClaudeBot` / `anthropic-ai` before adding.
- **`max_uses: 2`** on `web_search` is deliberate — keeps per-topic cost bounded. Raising it multiplies token spend by topic count.
- **Pushing changes to `main`** triggers nothing by itself; only the cron and `workflow_dispatch` invoke the digest. But the in-browser editor commits to `main` too, so always `git pull --rebase` before pushing local changes — the user may have edited config from the browser.
- **No `gh` CLI is installed in this dev environment** and there's no PAT on disk. To trigger a workflow run, ask the user to click "Run workflow" in the Actions tab, or have them paste a PAT via the `!`-prefix shell.
