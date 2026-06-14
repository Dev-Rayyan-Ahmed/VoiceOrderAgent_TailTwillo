import os
import sys
import datetime
import json
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("MODEL_API_KEY")
n8n_url = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook-test/restaurant-orders")

if not api_key:
    print("❌ Error: MODEL_API_KEY not found.")
    sys.exit(1)

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=api_key,
)

# --- UPGRADED SYSTEM PROMPT ---
SYSTEM_PROMPT = """
You are an AI order-taking agent for a restaurant. You speak ONLY English.

STRICT RULES:
1. NEVER change the quantity of an item unless explicitly told.
2. Keep answers VERY short (1-2 sentences).
3. Upsell ONLY ONCE.
4. FULFILLMENT LOGIC: Before asking the user to confirm the final order, you MUST ask: "Will this be for pickup or delivery?"
5. IF DELIVERY: You MUST ask for their delivery address.
6. IF PICKUP: The address is simply "Pickup".
7. CHECKOUT: ONLY output a raw JSON string when the user says "confirm", "checkout", or "done" AND you have gathered the fulfillment method. Do not output any text other than the JSON.

The JSON MUST be exactly in this format:
{"status": "confirmed", "order_type": "delivery", "address": "123 Main St", "items": ["2x Beef Burger", "1x Small Fries"]}
"""

chat_history = [{"role": "system", "content": SYSTEM_PROMPT}]

print("=" * 60)
print("🎙️ Advanced Groq Console Tester - Pickup/Delivery Logic")
print("Try ordering, tell it delivery, give an address, then 'confirm'.")
print("=" * 60 + "\n")

initial_greeting = "Welcome to our restaurant! What would you like to order today?"
print(f"🤖 Bot: {initial_greeting}")
chat_history.append({"role": "assistant", "content": initial_greeting})

while True:
    try:
        user_input = input("\n🧑 You: ")
        
        if user_input.lower() in ['quit', 'exit']:
            sys.exit()
        if not user_input.strip():
            continue

        chat_history.append({"role": "user", "content": user_input})
        print("🤖 Bot is thinking...", end="\r") 

        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=chat_history,
            temperature=0.3
        )
        
        ai_response = response.choices[0].message.content.strip()
        print("\033[K🤖 Bot: " + ai_response)
        
        chat_history.append({"role": "assistant", "content": ai_response})

        # --- UPGRADED: JSON PARSER & FORMATTER ---
        if "{" in ai_response and "confirmed" in ai_response.lower():
            print("\n📦 Order detected! Parsing JSON payload...")
            
            # Reconstruct the transcript
            full_transcript = ""
            for msg in chat_history:
                if msg["role"] == "user":
                    full_transcript += f"Customer: {msg['content']}\n"
                elif msg["role"] == "assistant" and not "{" in msg["content"]:
                    full_transcript += f"Agent: {msg['content']}\n"
            
            # Safely extract and parse the JSON string
            try:
                start_idx = ai_response.find("{")
                end_idx = ai_response.rfind("}") + 1
                json_data = json.loads(ai_response[start_idx:end_idx])
                
                # Format the items array into a clean string with line breaks
                items_list = json_data.get("items", [])
                formatted_items = "\n".join(items_list)
                
                order_type = json_data.get("order_type", "pickup").title()
                address = json_data.get("address", "Pickup")

                payload = {
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "phone_number": "+1234567890", 
                    "call_sid": "CONSOLE_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                    "transcript": full_transcript.strip(),
                    "formatted_items": formatted_items, # <--- NEW!
                    "order_type": order_type,           # <--- NEW!
                    "address": address                  # <--- NEW!
                }
                
                print(f"📤 Pushing structured order payload to n8n webhook...")
                n8n_response = requests.post(n8n_url, json=payload)
                if n8n_response.status_code in [200, 201]:
                    print("✅ Successfully logged to n8n! Check your Google Sheet.")
                else:
                    print(f"⚠️ n8n responded with status code: {n8n_response.status_code}")
                    
            except json.JSONDecodeError:
                print("❌ Failed to parse the AI's JSON output.")
            except Exception as webhook_error:
                print(f"❌ Could not connect to n8n webhook: {webhook_error}")

    except KeyboardInterrupt:
        sys.exit()