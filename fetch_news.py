import json                      
import os                     
import time                                                                                                                                                                                                   
from datetime import datetime, timezone
                                                                                                                                                                                                              
from anthropic import Anthropic, RateLimitError                                                                                                                                                               
                                                                                                                                                                                                              
MODEL = "claude-sonnet-4-5"                                                                                                                                                                                   
TOOLS = [{"type": "web_search_20250305", "name": "web_search"}]                                                                                                                                               
                              
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
                             
                              
def fetch_topic(topic):    
    for attempt in range(4):                                                                                                                                                                                  
        try:              
            response = client.messages.create(                                                                                                                                                                
                model=MODEL,                                                                                                                                                                                  
                max_tokens=1024,
                tools=TOOLS,                                                                                                                                                                                  
                messages=[                                                       
                    {   
                        "role": "user",
                        "content": (
                            f"Summarize the most important news about '{topic}' "
                            "from the last 24 hours. Include 4-5 key points with sources."
                        ),             
                    }                                                                                                                                                                                         
                ],            
            )                                                                                                                                                                                                 
            parts = [block.text for block in response.content if block.type == "text"]
            return "\n\n".join(p for p in parts if p).strip()
        except RateLimitError:         
            wait = 60 * (attempt + 1)                                                                                                                                                                         
            print(f"Rate limited; sleeping {wait}s before retry...")
            time.sleep(wait)                                                                                                                                                                                  
    raise RuntimeError(f"Rate limited too many times for topic: {topic}")        
                              
                           
def main():                                                                                                                                                                                                   
    with open("topics.json") as f:
        topics = json.load(f)["topics"]                                                                                                                                                                       
                                                                                                                                                                                                              
    digest = {                
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),                                                                                                                            
        "topics": [],                                                            
    }                     
                              
    for i, topic in enumerate(topics):                                                                                                                                                                        
        if i > 0:            
            time.sleep(30)                                                                                                                                                                                    
        print(f"Fetching: {topic}")                                              
        summary = fetch_topic(topic)                                                                                                                                                                          
        digest["topics"].append({"title": topic, "summary": summary})
                                                                                                                                                                                                              
    with open("digest.json", "w") as f:                                          
        json.dump(digest, f, indent=2)
                                       
    print("Done! digest.json saved.")
                                                                                                                                                                                                              
                           
if __name__ == "__main__":                                                                                                                                                                                    
    main() 
