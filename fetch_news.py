import json
import os
import time
from datetime import datetime, timedelta, timezone

from anthropic import Anthropic, RateLimitError

MODEL = "claude-haiku-4-5-20251001"

CONFIG_PATH = "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def topic_title(entry):
    if isinstance(entry, dict):
        return (entry.get("title") or "").strip() or "(untitled topic)"
    return str(entry).strip() or "(untitled topic)"


def topic_focus(entry):
    if isinstance(entry, dict):
        return (entry.get("focus") or "").strip()
    return ""


def build_web_search():
    """Open-web search — no allowed_domains. Configured sources are passed
    into the prompt as a soft preference, so the model can search anywhere
    when none of the preferred publishers have the story."""
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 1,
    }


PIXEL_ART_EXAMPLE = (
    "............\n"
    "............\n"
    "....####....\n"
    "...#....#...\n"
    "..#.####.#..\n"
    "..#.####.#..\n"
    "..#......#..\n"
    "...#....#...\n"
    "....####....\n"
    "............\n"
    "............\n"
    "............"
)

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
                    "development in this topic over the last 24 hours. "
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
                                "'VentureBeat', 'The Decoder'."
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
                        "pixel_art": {
                            "type": "string",
                            "description": (
                                "A SIMPLE pixel-art silhouette you draw "
                                "yourself, representing the most concrete "
                                "subject of the story (a chip, a coin, a "
                                "robot, a flask, a rocket, a brain, a "
                                "datacenter rack, a satellite, etc.). "
                                "Format: EXACTLY 12 lines separated by '\\n', "
                                "each line EXACTLY 12 characters. Use '#' "
                                "for filled pixels (foreground) and '.' for "
                                "transparent background. Single colour "
                                "silhouette only — keep it chunky and "
                                "readable at small size. Centre the subject "
                                "with some empty padding around it. Example "
                                "for a gear-with-hole:\n" + PIXEL_ART_EXAMPLE
                            ),
                        },
                    },
                    "required": [
                        "headline", "body", "source_url",
                        "source_name", "pixel_art",
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
    "Workflow per topic: (1) one web_search shaped by the topic's focus, "
    "(2) call submit_digest exactly once. "
    "HARD RULES: "
    "(a) Every story MUST be published within the last 24 hours of the "
    "current UTC time given. Drop anything stale or unconfirmed — fewer "
    "stories is fine, zero is fine. "
    "(b) Every source_url MUST be a real URL that appeared in your "
    "web_search results — never invent, guess, or extrapolate URLs. "
    "(c) For each story draw a simple 12x12 `pixel_art` silhouette of the "
    "story's most concrete subject (use '#' for filled, '.' for empty). "
    "(d) If NO story passes the 24h bar, still call submit_digest, with "
    "stories=[] and executive_summary EXACTLY (substituting a natural "
    "topic phrase for {TOPIC}): "
    "\"No major {TOPIC} news was published in the last 24 hours that "
    "could be confirmed through search results, suggesting a quiet day "
    "in the sector.\" "
    "Examples of {TOPIC} substitution: Startups → \"startup\"; "
    "Deep Tech → \"deep tech\"; AI Infrastructure → "
    "\"AI infrastructure\"; AI Announcements → \"AI announcement\"."
)

USER_TMPL = (
    "Topic: {title}\n"
    "{focus_line}"
    "{sources_line}"
    "Current UTC time: {now_utc}\n"
    "Window: include ONLY stories published since {since_utc} "
    "(strictly the last 24 hours).\n\n"
    "Search the web for what mattered in this topic in that 24-hour window, "
    "then call submit_digest with the structured briefing."
)

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def fetch_topic(entry, tools, sources):
    title = topic_title(entry)
    focus = topic_focus(entry)
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    sources_line = ""
    if sources:
        sources_line = (
            "Preferred publishers (try these first, but search the wider "
            "web freely if none of them have the story): "
            + ", ".join(sources) + "\n"
        )
    user_msg = USER_TMPL.format(
        title=title,
        focus_line=(f"Focus: {focus}\n" if focus else ""),
        sources_line=sources_line,
        now_utc=now.strftime("%Y-%m-%d %H:%M UTC"),
        since_utc=since.strftime("%Y-%m-%d %H:%M UTC"),
    )
    for attempt in range(4):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1536,
                system=SYSTEM,
                tools=tools,
                messages=[{"role": "user", "content": user_msg}],
            )
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name == "submit_digest":
                    return block.input
            text = "\n\n".join(
                b.text for b in response.content
                if getattr(b, "type", None) == "text" and getattr(b, "text", None)
            ).strip()
            return {"executive_summary": text, "stories": []}
        except RateLimitError:
            wait = 60 * (attempt + 1)
            print(f"Rate limited; sleeping {wait}s before retry...")
            time.sleep(wait)
    raise RuntimeError(f"Failed too many times for topic: {title}")


def main():
    cfg = load_config()
    topics = cfg.get("topics", [])
    sources = cfg.get("sources", []) or []
    tools = [build_web_search(), SUBMIT]

    digest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "topics": [],
    }

    for i, entry in enumerate(topics):
        if i > 0:
            time.sleep(30)
        title = topic_title(entry)
        print(f"Fetching: {title}")
        result = fetch_topic(entry, tools, sources)
        digest["topics"].append({
            "title": title,
            "executive_summary": result.get("executive_summary", ""),
            "stories": result.get("stories", []),
        })

    with open("digest.json", "w") as f:
        json.dump(digest, f, indent=2)

    print("Done! digest.json saved.")


if __name__ == "__main__":
    main()
