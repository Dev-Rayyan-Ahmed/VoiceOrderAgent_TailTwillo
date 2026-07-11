import os
import sys
import json
import base64
import asyncio
import audioop
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response
from dotenv import load_dotenv
import websockets

# ==========================================
# ⚙️ SYSTEM INITIALIZATION
# ==========================================
load_dotenv()
app = FastAPI()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("❌ Error: GEMINI_API_KEY not found in .env")
    sys.exit(1)

# The Gemini Multimodal Live API WebSocket URL
GEMINI_WS_URL = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={GEMINI_API_KEY}"

# Mock Menu
LIVE_MENU = """
- Zinger Burger: Rs 550
- Beef Burger: Rs 600
- French Fries: Rs 200
- Cold Drink: Rs 150
"""

# 🧠 THE URDU NATIVE PROMPT
SYSTEM_PROMPT = f"""
You are an AI order-taking agent for a restaurant in Karachi. 
You speak ONLY conversational Urdu, but use common English words for menu items (like "Zinger Burger", "Fries", "Order").
Be warm, polite, and very brief. Do not use asterisks or emojis because this is a voice call.

Current Menu:
{LIVE_MENU}

Ask the user what they want. When they are done, calculate the total in Rupees and tell them.
"""

# ==========================================
# 📞 FASTAPI ENDPOINTS
# ==========================================

@app.post("/incoming_call")
async def incoming_call(request: Request):
    """Answers the Twilio call and opens the WebSocket stream."""
    
    RAW_DOMAIN = "lappy-win-3m6d0ucbigd.tailb03040.ts.net" 
    CLEAN_DOMAIN = RAW_DOMAIN.replace("https://", "").replace("http://", "").split("/")[0]
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Say voice="alice">Connecting to the kitchen...</Say>
        <Connect>
            <Stream url="wss://{CLEAN_DOMAIN}/twilio/stream" />
        </Connect>
    </Response>
    """
    return Response(content=twiml, media_type="application/xml")

@app.websocket("/twilio/stream")
async def twilio_stream(twilio_ws: WebSocket):
    """The central pipe handling audio between Twilio and Gemini."""
    await twilio_ws.accept()
    print("✅ Twilio phone connected to server.")

    # --- FIX: Wait for Twilio's Start Event FIRST ---
    # This prevents the race condition where Gemini speaks before we have the SID
    stream_sid = None
    while True:
        data = await twilio_ws.receive_text()
        msg = json.loads(data)
        if msg['event'] == 'connected':
            continue
        if msg['event'] == 'start':
            stream_sid = msg['start']['streamSid']
            print(f"🎙️ Twilio Audio Stream Started: {stream_sid}")
            break

    # Connect to Google Gemini's Multimodal Live API
    async with websockets.connect(GEMINI_WS_URL) as gemini_ws:
        print("✅ Connected to Gemini Live API.")

        # 1. Send the Initial Setup
        setup_msg = {
            "setup": {
                "model": "models/gemini-3.1-flash-live-preview",
                "systemInstruction": {
                    "parts": [{"text": SYSTEM_PROMPT}]
                },
                "generationConfig": {
                    "responseModalities": ["AUDIO"] 
                }
            }
        }
        await gemini_ws.send(json.dumps(setup_msg))
        await gemini_ws.recv() 
        print("🧠 Gemini Session Initialized.")

        # 2. Trigger the automatic greeting
        trigger_msg = {
            "clientContent": {
                "turns": [{
                    "role": "user",
                    "parts": [{"text": "Greet the customer warmly in Urdu and ask for their order."}]
                }],
                "turnComplete": True
            }
        }
        await gemini_ws.send(json.dumps(trigger_msg))
        print("📢 Initial greeting triggered.")

        # Audio state trackers for smooth conversion
        in_state, out_state = None, None

        # --- TASK 1: Listen to Twilio & Send to Gemini ---
        async def receive_from_twilio():
            nonlocal in_state
            try:
                while True:
                    data = await twilio_ws.receive_text()
                    msg = json.loads(data)

                    if msg['event'] == 'media':
                        chunk = base64.b64decode(msg['media']['payload'])
                        pcm_audio = audioop.ulaw2lin(chunk, 2)
                        pcm_16k, in_state = audioop.ratecv(pcm_audio, 2, 1, 8000, 16000, in_state)
                        
                        # Sent using the updated 'audio' payload format
                        gemini_msg = {
                            "realtimeInput": {
                                "audio": {       
                                    "mimeType": "audio/pcm;rate=16000",
                                    "data": base64.b64encode(pcm_16k).decode("utf-8")
                                }
                            }
                        }
                        await gemini_ws.send(json.dumps(gemini_msg))
                        
                    elif msg['event'] == 'stop':
                        print("🛑 Twilio hung up.")
                        break

            except WebSocketDisconnect:
                print("🛑 Twilio disconnected.")
            except Exception as e:
                print(f"Twilio Receive Error: {e}")

        # --- TASK 2: Listen to Gemini & Send to Twilio ---
        async def receive_from_gemini():
            nonlocal out_state
            try:
                async for message in gemini_ws:
                    response = json.loads(message)
                    
                    if "serverContent" in response:
                        model_turn = response["serverContent"].get("modelTurn")
                        if model_turn:
                            for part in model_turn.get("parts", []):
                                if "inlineData" in part:
                                    gemini_audio_b64 = part["inlineData"]["data"]
                                    gemini_pcm = base64.b64decode(gemini_audio_b64)
                                    
                                    pcm_8k, out_state = audioop.ratecv(gemini_pcm, 2, 1, 24000, 8000, out_state)
                                    ulaw_data = audioop.lin2ulaw(pcm_8k, 2)
                                    
                                    twilio_msg = {
                                        "event": "media",
                                        "streamSid": stream_sid,
                                        "media": {
                                            "payload": base64.b64encode(ulaw_data).decode("utf-8")
                                        }
                                    }
                                    await twilio_ws.send_text(json.dumps(twilio_msg))
                                    
            except Exception as e:
                print(f"Gemini Receive Error: {e}")

        # Run both tasks concurrently
        await asyncio.gather(receive_from_twilio(), receive_from_gemini())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)