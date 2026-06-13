import os
import sys
import datetime
import json
import requests  
from dotenv import load_dotenv
from openai import OpenAI

# 1. Load environment variables from a .env file
load_dotenv()

# 2. Retrieve environment variables
api_key = os.getenv("MODEL_API_KEY")
# Get your local n8n URL from .env, or default to localhost if not specified
n8n_url = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook-test/restaurant-orders")

if not api_key:
    print("❌ Error: MODEL_API_KEY not found. Please check your .env file.")
    sys.exit(1)

print("🚀 Groq API Key loaded successfully. Connecting to GroqCloud...")

# 3. Initialize the client using Groq's OpenAI-compatible endpoint
client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=api_key,
)

SYSTEM_PROMPT = """
You are an AI order-taking agent for a restaurant. You speak ONLY English.

STRICT RULES:
1. NEVER change the quantity of an item unless the user explicitly tells you to. If they say "a burger", it means exactly ONE burger.
2. Keep answers VERY short (1-2 sentences).
3. Upsell ONLY ONCE.
4. If the user changes their order (adds or removes items), simply repeat the new order back to them and ask if they need anything else. Do NOT say "confirming" yet.
5. ONLY output a JSON string when the user explicitly says "confirm", "checkout", or "done".

--- EXAMPLES OF YOUR BEHAVIOR ---

Customer: "Hi, I want to order."
Agent: "Welcome! What would you like to order today?"

Customer: "I want one beef burger."
Agent: "One beef burger. Would you like to add fries or a cold drink to that?"

Customer: "Yes, add fries."
Agent: "Got it, one beef burger and fries. Anything else?"

Customer: "Actually, remove the fries."
Agent: "No problem. Just one beef burger. Are you ready to confirm the order?"

Customer: "Yes, confirm."
Agent: {"status": "confirmed", "items": ["beef burger"]}
"""

chat_history = [{"role": "system", "content": SYSTEM_PROMPT}]

print("=" * 60)
print("🎙️ Groq Console Tester + n8n Push - Restaurant Voice Agent")
print("Try ordering a burger, letting it upsell, then say 'confirm'.")
print("Type 'quit' or 'exit' to close.")
print("=" * 60 + "\n")

initial_greeting = "Welcome to our restaurant! What would you like to order today?"
print(f"🤖 Bot: {initial_greeting}")
chat_history.append({"role": "assistant", "content": initial_greeting})

while True:
    try:
        user_input = input("\n🧑 You: ")
        
        if user_input.lower() in ['quit', 'exit']:
            print("\nShutting down tester. Goodbye!")
            sys.exit()
            
        if not user_input.strip():
            continue

        chat_history.append({"role": "user", "content": user_input})
        print("🤖 Bot is thinking...", end="\r") 

        # 4. Call Groq's high-speed engine
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=chat_history,
            temperature=0.3
        )
        
        ai_response = response.choices[0].message.content.strip()
        print("\033[K🤖 Bot: " + ai_response)
        
        chat_history.append({"role": "assistant", "content": ai_response})

        # --- NEW: Check if the AI returned a JSON order payload ---
        if ai_response.startswith("{") and "confirmed" in ai_response.lower():
            print("\n📦 Order detected! Compiling history for n8n...")
            
            # Reconstruct the entire clean dialog transcript to pass over
            full_transcript = ""
            for msg in chat_history:
                if msg["role"] == "user":
                    full_transcript += f"Customer: {msg['content']}\n"
                elif msg["role"] == "assistant" and not msg["content"].startswith("{"):
                    full_transcript += f"Agent: {msg['content']}\n"
            
            # Format the exact JSON payload n8n expects
            payload = {
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "phone_number": "+1234567890 (Console-Test)", 
                "call_sid": "CONSOLE_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                "transcript": full_transcript.strip()
            }
            
            # Send the payload via HTTP POST directly to n8n
            try:
                print(f"📤 Pushing order payload to n8n webhook...")
                n8n_response = requests.post(n8n_url, json=payload)
                if n8n_response.status_code in [200, 201]:
                    print("✅ Successfully logged to n8n! Check your Google Sheet.")
                else:
                    print(f"⚠️ n8n responded with status code: {n8n_response.status_code}")
            except Exception as webhook_error:
                print(f"❌ Could not connect to n8n webhook: {webhook_error}")
                print("Make sure n8n is running locally (`npx n8n`) and the URL is right.")

    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit()
    except Exception as e:
        print(f"\n❌ Error connecting to Groq: {e}")
        sys.exit()