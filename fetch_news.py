import json
import os
import re
import time
from datetime import datetime, timezone

from anthropic import Anthropic, BadRequestError, RateLimitError

MODEL = "claude-sonnet-4-5"

CONFIG_PATH = "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_web_search(sources):
    tool = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 2,
    }
    if sources:
        tool["allowed_domains"] = sources
    return tool


_BLOCKED_DOMAINS_RE = re.compile(r"not accessible to our user agent:\s*\[([^\]]+)\]")


def _parse_blocked_domains(message):
    m = _BLOCKED_DOMAINS_RE.search(message)
    if not m:
        return []
    return [d.strip().strip("'\"") for d in m.group(1).split(",") if d.strip()]


def _strip_blocked(tools, blocked):
    blocked_set = set(blocked)
    out = []
    for t in tools:
        if t.get("type") == "web_search_20250305" and "allowed_domains" in t:
            kept = [d for d in t["allowed_domains"] if d not in blocked_set]
            nt = dict(t)
            if kept:
                nt["allowed_domains"] = kept
            else:
                nt.pop("allowed_domains", None)
            out.append(nt)
        else:
            out.append(t)
    return out

SUBMIT = {
    "name": "submit_digest",
    "description": (
        "Submit the structured briefing for the topic. Call this exactly "
        "once, AFTER gathering sources via web_search."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "executive_summary": {
                "type": "string",
                "description": (
                    "One or two crisp sentences capturing the most important "
                    "development in this topic over the last 24-48 hours. "
                    "Plain English, no hedging, no fluff."
                ),
            },
            "stories": {
                "type": "array",
                "minItems": 3,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "headline": {
                            "type": "string",
                            "description": "Punchy 6-12 word headline.",
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "1-2 sentence plain-English summary of the "
                                "story. Concrete, specific, no filler."
                            ),
                        },
                        "source_url": {
                            "type": "string",
                            "description": (
                                "Real URL of the source article. Use ONLY "
                                "URLs that appeared in your web_search "
                                "results. Never invent or guess URLs."
                            ),
                        },
                        "source_name": {
                            "type": "string",
                            "description": (
                                "Publisher name, e.g. 'Reuters', 'TechCrunch', "
                                "'Bloomberg'."
                            ),
                        },
                    },
                    "required": ["headline", "body", "source_url", "source_name"],
                },
            },
        },
        "required": ["executive_summary", "stories"],
    },
}


SYSTEM = (
    "You are a Morning Brew-style editor producing an executive briefing. "
    "Tone: clear, conversational, lightly witty, no jargon, no filler. "
    "For each topic, you MUST: (1) use web_search to find the most important "
    "news of the last 24-48 hours, then (2) call submit_digest exactly once "
    "with an executive summary and 3-5 distinct stories. "
    "Every source_url must be a real URL that appeared in your web_search "
    "results - never invent or guess URLs."
)

USER_TMPL = (
    "Topic: {topic}\n\n"
    "Search the web for what mattered in this topic over the last 24-48 hours, "
    "then call submit_digest with the structured briefing."
)

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def fetch_topic(topic, tools):
    """Return (result_dict, tools). The returned tools may have lost
    domains that block Anthropic's crawler, so the caller should reuse
    it for subsequent topics to avoid hitting the same 400 again."""
    for attempt in range(4):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM,
                tools=tools,
                messages=[
                    {"role": "user", "content": USER_TMPL.format(topic=topic)}
                ],
            )
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "submit_digest":
                    return block.input, tools
            text = "\n\n".join(
                b.text for b in response.content
                if getattr(b, "type", None) == "text" and getattr(b, "text", None)
            ).strip()
            return {"executive_summary": text, "stories": []}, tools
        except RateLimitError:
            wait = 60 * (attempt + 1)
            print(f"Rate limited; sleeping {wait}s before retry...")
            time.sleep(wait)
        except BadRequestError as e:
            blocked = _parse_blocked_domains(str(e))
            if not blocked:
                raise
            print(f"Sites blocking Anthropic web_search: {blocked}; pruning and retrying")
            tools = _strip_blocked(tools, blocked)
    raise RuntimeError(f"Failed too many times for topic: {topic}")


def main():
    cfg = load_config()
    topics = cfg.get("topics", [])
    sources = cfg.get("sources", []) or []
    tools = [build_web_search(sources), SUBMIT]

    digest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "topics": [],
    }

    for i, topic in enumerate(topics):
        if i > 0:
            time.sleep(30)
        print(f"Fetching: {topic}")
        result, tools = fetch_topic(topic, tools)
        digest["topics"].append({
            "title": topic,
            "executive_summary": result.get("executive_summary", ""),
            "stories": result.get("stories", []),
        })

    with open("digest.json", "w") as f:
        json.dump(digest, f, indent=2)

    print("Done! digest.json saved.")


if __name__ == "__main__":
    main()
