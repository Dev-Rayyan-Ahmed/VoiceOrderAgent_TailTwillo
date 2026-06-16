from fastapi import FastAPI, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
import json
import csv
import os
import sys
import datetime
import requests
from io import StringIO
from dotenv import load_dotenv

# ==========================================
# ⚙️ SYSTEM INITIALIZATION
# ==========================================
load_dotenv()

app = FastAPI()

api_key = os.getenv("MODEL_API_KEY")
n8n_url = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook-test/restaurant-orders")
MENU_CSV_URL = os.getenv("LIVE_MENU_LINK")

if not api_key:
    print("❌ Error: MODEL_API_KEY not found in .env")
    sys.exit(1)

# Using Groq for ultra-low latency voice responses
client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=api_key,
)

# 🧠 MULTI-USER MEMORY DICTIONARY
# This stores separate chat histories for every single active phone call
active_calls = {}

# ==========================================
# 🛠️ HELPER FUNCTIONS
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

def get_live_menu():
    try:
        response = requests.get(MENU_CSV_URL)
        response.raise_for_status()
        csv_data = csv.reader(StringIO(response.text))
        next(csv_data, None) # Skip header
        menu_text = "CURRENT MENU AND PRICES:\n"
        for row in csv_data:
            if len(row) >= 3 and row[1].strip() != "": 
                menu_text += f"- {row[1].strip()}: Rs {row[2].strip()}\n"
        return menu_text
    except Exception as e:
        print(f"⚠️ Warning: Could not fetch menu. ({e})")
        return "- Beef Burger: Rs 600\n- Cold Drink: Rs 150"

live_menu_string = get_live_menu()
print("✅ Live Menu Loaded Successfully!")

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

# ==========================================
# 📞 FASTAPI ENDPOINTS
# ==========================================

@app.post("/incoming_call")
async def incoming_call(request: Request):
    """Triggered the second you dial the Twilio number."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    
    print(f"\n📞 NEW CALL STARTED - SID: {call_sid}")
    
    # Initialize a fresh memory block just for this specific caller
    active_calls[call_sid] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    initial_greeting = "Welcome to our restaurant! What would you like to order today?"
    active_calls[call_sid].append({"role": "assistant", "content": initial_greeting})
    
    response = VoiceResponse()
    gather = Gather(input="speech", action="/process_speech", speechTimeout=1)
    gather.say(initial_greeting, voice="Google.en-US-Neural2-F")
    response.append(gather)
    response.redirect('/incoming_call')
    
    return Response(content=str(response), media_type="application/xml")

@app.post("/process_speech")
async def process_speech(request: Request):
    form_data = await request.form()
    user_text = form_data.get("SpeechResult", "")
    caller_number = form_data.get("From", "Unknown Number")
    call_sid = form_data.get("CallSid")

    print(f"👤 Caller [{caller_number}] said: {user_text}")

    # Safety catch if Twilio sends speech for a call that dropped/restarted
    if call_sid not in active_calls:
        active_calls[call_sid] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # 1. Add user speech to this caller's specific memory
    active_calls[call_sid].append({"role": "user", "content": user_text})

    # 2. Call the LLM
    ai_response = client.chat.completions.create(
        model="llama-3.1-8b-instant", 
        messages=active_calls[call_sid],
        temperature=0.3
    )

    bot_reply = ai_response.choices[0].message.content.strip()
    active_calls[call_sid].append({"role": "assistant", "content": bot_reply})

    print(f"🤖 AI Replies: {bot_reply}")

    # ==========================================
    # 🚨 CHECKOUT & N8N PUSH LOGIC
    # ==========================================
    if "{" in bot_reply and "confirmed" in bot_reply.lower():
        print(f"✅ Order confirmed for {caller_number}! Parsing and sending to n8n...")
        
        # Build transcript for this specific caller
        full_transcript = ""
        for msg in active_calls[call_sid]:
            if msg["role"] == "user":
                full_transcript += f"Customer: {msg['content']}\n"
            elif msg["role"] == "assistant" and not "{" in msg["content"]:
                full_transcript += f"Agent: {msg['content']}\n"
                
        try:
            start_idx = bot_reply.find("{")
            end_idx = bot_reply.rfind("}") + 1
            json_data = json.loads(bot_reply[start_idx:end_idx])
            
            items_list = json_data.get("items", [])
            formatted_items = "\n".join(items_list)
            order_type = json_data.get("order_type", "pickup").title()
            address = json_data.get("address", "Pickup")
            total_price = json_data.get("total_price", 0)
            
            unique_order_id = generate_daily_order_id()

            payload = {
                "order_id": unique_order_id,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "phone_number": f"'{caller_number}", 
                "call_sid": call_sid,
                "transcript": full_transcript.strip(),
                "formatted_items": formatted_items,
                "order_type": order_type,
                "address": address,
                "total_price": f"Rs {total_price}"
            }
            
            # Post to n8n
            requests.post(n8n_url, json=payload)
            print(f"📤 Sent Order {unique_order_id} to database!")
            
        except Exception as e:
            print(f"❌ Failed to process JSON or webhook: {e}")

        # Hang up the call
        response = VoiceResponse()
        response.say("Your order has been confirmed and sent to our kitchen. Thank you for choosing us, have a great day!", voice="Google.en-US-Neural2-F")
        response.hangup()
        
        # Free up server memory by deleting this call's history
        active_calls.pop(call_sid, None)
        
        return Response(content=str(response), media_type="application/xml")

    # ==========================================
    # 🤖 NORMAL CONVERSATION LOOP
    # ==========================================
    response = VoiceResponse()
    gather = Gather(input="speech", action="/process_speech", speechTimeout=1)
    gather.say(bot_reply, voice="Google.en-US-Neural2-F")
    response.append(gather)
    response.redirect('/incoming_call')
    
    return Response(content=str(response), media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)