 import json                                                                                                                                                                                                 
  import os                                                                                                                                                                                                   
  from datetime import datetime, timezone                                                                                                                                                                     
                                                                                                                                                                                                              
  from anthropic import Anthropic                                                                                                                                                                             
                                                                                                                                                                                                              
  MODEL = "claude-sonnet-4-20250514"                                               
  TOOLS = [{"type": "web_search_20250305", "name": "web_search"}]                                                                                                                                             
                                                                                   
  client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
                                                                                                                                                                                                              
                             
  def fetch_topic(topic):                                                                                                                                                                                     
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
                                                                                                                                                                                                              
                                
  def main():                                                                                                                                                                                                 
      with open("topics.json") as f:                                               
          topics = json.load(f)["topics"]
                                         
      digest = {                                                                                                                                                                                              
          "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
          "topics": [],                                                                                                                                                                                       
      }                                                                            
                            
      for topic in topics:      
          print(f"Fetching: {topic}")                                                                                                                                                                         
          summary = fetch_topic(topic)
          digest["topics"].append({"title": topic, "summary": summary})                                                                                                                                       
                                                                                   
      with open("digest.json", "w") as f:                                                                                                                                                                     
          json.dump(digest, f, indent=2)
                                                                                                                                                                                                              
      print("Done! digest.json saved.")                                            
                            
                                         
  if __name__ == "__main__":                                                                                                                                                                                  
      main()
