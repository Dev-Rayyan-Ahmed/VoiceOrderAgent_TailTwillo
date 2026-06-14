import os
import sys
import datetime
import json
import requests
import csv
from io import StringIO
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

api_key = os.getenv("MODEL_API_KEY")
n8n_url = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook-test/restaurant-orders")
MENU_CSV_URL = os.getenv("LIVE_MENU_LINK")

if not api_key:
    print("❌ Error: MODEL_API_KEY not found.")
    sys.exit(1)

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=api_key,
)

# ==========================================
# 🔢 DAILY ORDER ID GENERATOR
# ==========================================
def generate_daily_order_id():
    """Generates a sequential ID like ORD-20260614-001 that resets daily."""
    tracker_file = "order_sequence.json"
    today_str = datetime.datetime.now().strftime("%Y%m%d") 
    
    tracker = {"date": today_str, "last_sequence": 0}
    
    if os.path.exists(tracker_file):
        try:
            with open(tracker_file, "r") as f:
                tracker = json.load(f)
        except Exception:
            pass 
            
    if tracker["date"] != today_str:
        tracker["date"] = today_str
        tracker["last_sequence"] = 0
        
    tracker["last_sequence"] += 1
    
    with open(tracker_file, "w") as f:
        json.dump(tracker, f)
        
    sequence_str = str(tracker["last_sequence"]).zfill(3)
    return f"ORD-{today_str}-{sequence_str}"

# ==========================================
# 🍔 FETCH LIVE MENU FROM GOOGLE SHEETS
# ==========================================
def get_live_menu():
    try:
        response = requests.get(MENU_CSV_URL)
        response.raise_for_status()
        
        # Parse the CSV text into a readable string for the AI
        csv_data = csv.reader(StringIO(response.text))
        
        # SKIP THE HEADER ROW (S.No | Item Name | Unit Price in PKR)
        next(csv_data, None) 
        
        menu_text = "CURRENT MENU AND PRICES:\n"
        for row in csv_data:
            # Check if the row has at least 3 columns (S.No, Name, Price)
            # and ensure the Item Name isn't empty
            if len(row) >= 3 and row[1].strip() != "": 
                item_name = row[1].strip()
                item_price = row[2].strip()
                menu_text += f"- {item_name}: Rs {item_price}\n"
                
        return menu_text
    except Exception as e:
        print(f"⚠️ Warning: Could not fetch menu. Using fallback. ({e})")
        return "- Beef Burger: Rs 600\n- Small Fries: Rs 200\n- Cold Drink: Rs 150"
    
live_menu_string = get_live_menu()
print("✅ Live Menu Loaded Successfully!")
print(live_menu_string)

# ==========================================
# 🧠 DYNAMIC SYSTEM PROMPT (IRONCLAD)
# ==========================================
SYSTEM_PROMPT = f"""
You are an AI order-taking agent for a restaurant. You speak ONLY English.

{live_menu_string}

STRICT OPERATING RULES:
1. ZERO HALLUCINATION: You can ONLY sell, suggest, or price items that are EXACTLY listed in the menu above. 
2. OFF-MENU REQUESTS: If the user asks for something not on the menu (e.g., drinks, if none are listed), you MUST politely apologize and state that it is currently unavailable. NEVER invent items or prices.
3. UPSELL: Upsell ONLY ONCE, and you must pick a valid, existing item from the provided menu.
4. QUANTITIES & CLARIFICATION: If the user explicitly states a number (e.g., "2 burgers" or "a pizza"), accept it. If they ask for an item but do NOT mention a quantity (e.g., "I want fries" or "Add zinger burgers"), you MUST pause and ask "How many [item] would you like?" before moving on. Never guess or assume the quantity if it is ambiguous.
5. FULFILLMENT & CALCULATION: Before concluding, ask "Will this be for pickup or delivery?" (If delivery, get the address. If pickup, address is "Pickup"). You MUST calculate and state the final total estimated price based ONLY on the provided menu prices.
6. CHECKOUT & JSON: ONLY output a raw JSON string when the user says "confirm", "checkout", or "done" AND you have the address. Do not output any conversational text alongside the JSON.
7. JSON MEMORY ACCURACY: Before generating the JSON, review the entire chat history. The `items` array MUST contain every single confirmed item and its exact quantity. Do not drop items.

JSON FORMAT EXACTLY LIKE THIS:
{{"status": "confirmed", "order_type": "delivery", "address": "123 Main St", "items": ["1x Chicken Fajita Pizza (Large)", "3x Cold Drink"], "total_price": 2050}}
"""

chat_history = [{"role": "system", "content": SYSTEM_PROMPT}]

print("=" * 60)
print("🎙️ Advanced Groq Tester - Live Menu + Order IDs")
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

        # --- JSON PARSER & FORMATTER ---
        if "{" in ai_response and "confirmed" in ai_response.lower():
            print("\n📦 Order detected! Generating ID and parsing JSON...")
            
            full_transcript = ""
            for msg in chat_history:
                if msg["role"] == "user":
                    full_transcript += f"Customer: {msg['content']}\n"
                elif msg["role"] == "assistant" and not "{" in msg["content"]:
                    full_transcript += f"Agent: {msg['content']}\n"
            
            try:
                start_idx = ai_response.find("{")
                end_idx = ai_response.rfind("}") + 1
                json_data = json.loads(ai_response[start_idx:end_idx])
                
                items_list = json_data.get("items", [])
                formatted_items = "\n".join(items_list)
                order_type = json_data.get("order_type", "pickup").title()
                address = json_data.get("address", "Pickup")
                total_price = json_data.get("total_price", 0)
                
                # USING THE NEW TRACKER FUNCTION HERE
                unique_order_id = generate_daily_order_id()

                payload = {
                    "order_id": unique_order_id,
                    "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "phone_number": "'+1234567890 (Console-Test)", 
                    "call_sid": "CONSOLE_" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                    "transcript": full_transcript.strip(),
                    "formatted_items": formatted_items,
                    "order_type": order_type,
                    "address": address,
                    "total_price": f"Rs {total_price}"
                }
                
                print(f"📤 Pushing order [{unique_order_id}] to n8n webhook...")
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