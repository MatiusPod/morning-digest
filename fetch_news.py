import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from anthropic import Anthropic, BadRequestError, RateLimitError

ICON_ENUM = [
    "chip", "robot", "brain", "rocket", "coin", "server",
    "flask", "shield", "chat", "bolt", "gear", "eye",
    "atom", "dna", "satellite", "default",
]

MODEL = "claude-haiku-4-5-20251001"

CONFIG_PATH = "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def build_web_search(sources):
    tool = {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 1,
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
                "minItems": 0,
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
                                "Publisher name, e.g. 'TechCrunch', "
                                "'Bloomberg', 'The Decoder'."
                            ),
                        },
                        "published_at": {
                            "type": "string",
                            "description": (
                                "Publication date/time of the source article "
                                "in ISO 8601 if known (e.g. "
                                "'2026-05-23T14:30:00Z'). MUST be within the "
                                "last 24 hours of the current UTC time."
                            ),
                        },
                        "icon": {
                            "type": "string",
                            "enum": ICON_ENUM,
                            "description": (
                                "Pixel-art icon category that best represents "
                                "the story. Pick the single best fit: "
                                "chip (silicon/hardware), robot (robotics/"
                                "agents), brain (research/cognition), rocket "
                                "(launch/announcement), coin (funding/M&A), "
                                "server (infra/datacenter/cloud), flask "
                                "(science/lab), shield (safety/security/"
                                "regulation), chat (chatbot/LLM/assistant), "
                                "bolt (speed/breakthrough/benchmark), gear "
                                "(engineering/tooling), eye (vision/perception/"
                                "multimodal), atom (deep tech/quantum/"
                                "materials), dna (biotech/health), satellite "
                                "(space/communications), default (only if "
                                "nothing else fits)."
                            ),
                        },
                    },
                    "required": [
                        "headline", "body", "source_url",
                        "source_name", "icon",
                    ],
                },
            },
        },
        "required": ["executive_summary", "stories"],
    },
}


SYSTEM = (
    "You are a Morning Brew-style editor producing an executive briefing. "
    "Tone: clear, conversational, lightly witty, no jargon, no filler. "
    "Workflow per topic: (1) one web_search for the most important news "
    "from the last 24 hours, (2) call submit_digest exactly once. "
    "HARD RULES: "
    "(a) Every story MUST be published within the last 24 hours of the "
    "current UTC time given. Drop anything stale or unconfirmed — fewer "
    "stories is fine, zero is fine. "
    "(b) Every source_url MUST be a real URL that appeared in your "
    "web_search results — never invent, guess, or extrapolate URLs. "
    "(c) For each story set `icon` to the single best-fit category. "
    "(d) If NO story passes the 24h bar, still call submit_digest, with "
    "stories=[] and executive_summary EXACTLY (substituting a natural "
    "topic phrase for {TOPIC}): "
    "\"No major {TOPIC} news was published in the last 24 hours that "
    "could be confirmed through search results, suggesting a quiet day "
    "in the sector.\" "
    "Examples of {TOPIC} substitution: Startups → \"startup\"; "
    "Deep Tech → \"deep tech\"; AI infrastructure news → "
    "\"AI infrastructure\"; AI announcements & new models → "
    "\"AI announcement or new model\"."
)

USER_TMPL = (
    "Topic: {topic}\n"
    "Current UTC time: {now_utc}\n"
    "Window: include ONLY stories published since {since_utc} "
    "(strictly the last 24 hours).\n\n"
    "Search the web for what mattered in this topic in that 24-hour window, "
    "then call submit_digest with the structured briefing."
)

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def fetch_topic(topic, tools):
    """Return (result_dict, tools). The returned tools may have lost
    domains that block Anthropic's crawler, so the caller should reuse
    it for subsequent topics to avoid hitting the same 400 again."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    user_msg = USER_TMPL.format(
        topic=topic,
        now_utc=now.strftime("%Y-%m-%d %H:%M UTC"),
        since_utc=since.strftime("%Y-%m-%d %H:%M UTC"),
    )
    for attempt in range(4):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM,
                tools=tools,
                messages=[{"role": "user", "content": user_msg}],
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
