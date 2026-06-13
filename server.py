import os
from datetime import datetime
from fastapi import FastAPI, Form, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# --- INITIALIZE GROQ CLIENT ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

if not GROQ_API_KEY:
    raise ValueError("🚨 Missing GROQ_API_KEY in your environment variables!")

client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY
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

# --- FIX: Dictionary to isolate conversation histories by unique CallSid ---
call_sessions = {}

def generate_twiml_response(text_to_speak):
    """Generates the Twilio XML to speak and listen using Google Cloud integration."""
    response = VoiceResponse()
    
    # Securely gather speech input
    gather = Gather(input="speech", action="/process_speech", speechTimeout="auto")
    gather.say(text_to_speak, voice="Google.en-US-Neural2-F")
    response.append(gather)
    
    # If customer says nothing, loop back to avoid dropping the call
    response.redirect('/incoming_call')
    return response

@app.post("/incoming_call")
async def incoming_call(request: Request):
    """Triggered the exact second a user dials your Twilio number."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    
    # Initialize a completely unique history track for this caller instance
    call_sessions[call_sid] = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    initial_greeting = "Welcome to our restaurant! What would you like to order today?"
    call_sessions[call_sid].append({"role": "assistant", "content": initial_greeting})
    
    twiml = generate_twiml_response(initial_greeting)
    return Response(content=str(twiml), media_type="application/xml")

@app.post("/process_speech")
async def process_speech(request: Request):
    form_data = await request.form()
    user_text = form_data.get("SpeechResult", "")
    caller_number = form_data.get("From", "Unknown Number")
    call_sid = form_data.get("CallSid")

    # Safety fall-back if the webhook hits without initializing
    if call_sid not in call_sessions:
        call_sessions[call_sid] = [{"role": "system", "content": SYSTEM_PROMPT}]

    print(f"👤 Call [{call_sid}] from {caller_number} said: {user_text}")

    # ==========================================
    # 🚨 INTERCEPT CONFIRMATION (GUARDRAIL)
    # ==========================================
    if any(keyword in user_text.lower() for keyword in ["confirm", "checkout", "done"]):
        print(f"✅ Order confirmed for session [{call_sid}]. Sending payload to n8n...")
        
        # Formulate full readable call logs for transcription storage
        transcript = " | ".join([f"{msg['role']}: {msg['content']}" for msg in call_sessions[call_sid] if msg['role'] != 'system'])
        
        # Fire off payload to your n8n automation webhook asynchronously
        if N8N_WEBHOOK_URL:
            try:
                async with httpx.AsyncClient() as async_client:
                    await async_client.post(N8N_WEBHOOK_URL, json={
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "phone_number": caller_number,
                        "call_sid": call_sid,
                        "transcript": transcript
                    }, timeout=5.0)
            except Exception as e:
                print(f"⚠️ Failed to forward data to n8n: {e}")
        else:
            print("⚠️ N8N_WEBHOOK_URL is not set. Skipping automation pipeline.")

        # Wipe call trace clean from RAM allocation
        call_sessions.pop(call_sid, None)

        response = VoiceResponse()
        response.say("Your order has been confirmed and saved to our system. Thank you for choosing us, have a great day!", voice="Google.en-US-Neural2-F")
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    # ==========================================
    # 🤖 CORE LLM FLOW (GROQCLOUD INTERACTION)
    # ==========================================
    call_sessions[call_sid].append({"role": "user", "content": user_text})

    try:
        ai_response = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=call_sessions[call_sid],
            temperature=0.3
        )
        bot_reply = ai_response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Groq API Error: {e}")
        bot_reply = "I am sorry, I had trouble processing that. Can you repeat your item?"

    print(f"🤖 AI Session [{call_sid}] Replies: {bot_reply}")
    call_sessions[call_sid].append({"role": "assistant", "content": bot_reply})

    # Return standard voice interaction frame back to Twilio loop
    twiml = generate_twiml_response(bot_reply)
    return Response(content=str(twiml), media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)