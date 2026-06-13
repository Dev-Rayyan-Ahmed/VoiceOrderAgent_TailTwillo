from fastapi import FastAPI, Form, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
import json
import csv
from datetime import datetime
import os

app = FastAPI()
client = OpenAI(
    base_url="http://100.89.116.26:11434/v1",
    api_key="ollama"
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

#Global memory
conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]

def generate_twiml_response(text_to_speak):
    """Generates the Twilio XML to speak and listen using Google Cloud integration."""
    response = VoiceResponse()
    
    if text_to_speak.startswith("{") and "confirmed" in text_to_speak:
        print(f"✅ ORDER SAVED: {text_to_speak}")
        response.say("Your order has been confirmed. Thank you for calling!", voice="Google.en-US-Neural2-F")
        response.hangup()
        return response

    #Speech-to-Text
    gather = Gather(input="speech", action="/process_speech", speechTimeout="auto")
    
    #Google Cloud TTS Voice
    gather.say(text_to_speak, voice="Google.en-US-Neural2-F")
    
    response.append(gather)
    
    # If didn't said anything, loop back
    response.redirect('/incoming_call')
    return response

@app.post("/incoming_call")
async def incoming_call():
    """Triggered the second you dial the Twilio number."""
    global conversation_history
    # Reset history for a new call
    conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    initial_greeting = "Welcome to our restaurant! What would you like to order today?"
    
    #message to memory
    conversation_history.append({"role": "assistant", "content": initial_greeting})
    
    twiml = generate_twiml_response(initial_greeting)
    return Response(content=str(twiml), media_type="application/xml")

@app.post("/process_speech")
async def process_speech(request: Request):
    global conversation_history # Ensures we can reset the history for the next caller
    
    form_data = await request.form()
    user_text = form_data.get("SpeechResult", "")
    caller_number = form_data.get("From", "Unknown Number")

    print(f"👤 Caller said: {user_text}")

    # ==========================================
    # 🚨 THE MANUAL OVERRIDE (GUARDRAIL)
    # ==========================================
    if "confirm" in user_text.lower():
        print("✅ User confirmed!")
        
        # 1. Prepare the CSV File
        csv_filename = "orders.csv"
        file_exists = os.path.isfile(csv_filename)
        
        # 2. Write to CSV
        with open(csv_filename, mode='a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            # Add headers if it's a brand new file
            if not file_exists:
                writer.writerow(["Timestamp", "Phone Number", "Order Transcript"])
            
            # Format the conversation history into a readable receipt
            transcript = " | ".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history if msg['role'] != 'system'])
            
            # Save the row
            writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), caller_number, transcript])

        # 3. Tell Twilio to hang up the call
        response = VoiceResponse()
        response.say("Your order has been confirmed and saved to our system. Thank you for choosing us, have a great day!", voice="Google.en-US-Neural2-F")
        response.hangup()
        
        # 4. Wipe the memory clean for the next phone call!
        conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        return Response(content=str(response), media_type="application/xml")

    # ==========================================
    # 🤖 NORMAL LLM FLOW (If they didn't say confirm)
    # ==========================================
    conversation_history.append({"role": "user", "content": user_text})

    # Call your local PC model
    ai_response = client.chat.completions.create(
        model="my-dolphin-1B", 
        messages=conversation_history,
        temperature=0.3,
        stop=["\nCustomer:", "Customer:", "\n🧑 You:"]
    )

    bot_reply = ai_response.choices[0].message.content.strip()
    print(f"🤖 AI Replies: {bot_reply}")
    
    conversation_history.append({"role": "assistant", "content": bot_reply})

    #normal Twilio response
    response = VoiceResponse()
    gather = Gather(input="speech", action="/process_speech", speechTimeout="auto")
    gather.say(bot_reply, voice="Google.en-US-Neural2-F")
    response.append(gather)
    response.redirect('/incoming_call')
    
    return Response(content=str(response), media_type="application/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)