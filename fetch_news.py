import json
import os
import requests
from datetime import datetime

API_KEY = os.environ["PERPLEXITY_API_KEY"]
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def fetch_topic(topic):
    payload = {
        "model": "sonar",
        "messages": [
            {
                "role": "user",
                "content": f"Summarize the most important news about '{topic}' from the last 24 hours. Include 4-5 key points with sources."
            }
        ]
    }
    response = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers=HEADERS,
        json=payload
    )
    data = response.json()
    return data["choices"][0]["message"]["content"]

with open("topics.json") as f:
    topics = json.load(f)["topics"]

digest = {
    "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    "topics": []
}

for topic in topics:
    print(f"Fetching: {topic}")
    summary = fetch_topic(topic)
    digest["topics"].append({
        "title": topic,
        "summary": summary
    })

with open("digest.json", "w") as f:
    json.dump(digest, f, indent=2)

print("Done! digest.json saved.")
