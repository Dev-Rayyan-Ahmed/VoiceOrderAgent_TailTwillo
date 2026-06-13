import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

# 1. Load environment variables from a .env file
load_dotenv()

# 2. Retrieve your Groq API key
# Make sure your .env file has: GROQ_API_KEY=gsk_...
api_key = os.getenv("MODEL_API_KEY")

if not api_key:
    print("❌ Error: GROQ_API_KEY not found. Please check your .env file.")
    sys.exit(1)

print("🚀 Groq API Key loaded successfully. Connecting to GroqCloud...")

# 3. Initialize the client using Groq's OpenAI-compatible endpoint
client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=api_key,
)

# The EXACT Few-Shot System Prompt from your server code
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

# Initialize history with the system prompt
chat_history = [{"role": "system", "content": SYSTEM_PROMPT}]

print("=" * 60)
print("🎙️ Groq Console Tester - Restaurant Voice Agent")
print("Try ordering a burger, letting it upsell, then say 'confirm'.")
print("Type 'quit' or 'exit' to close.")
print("=" * 60 + "\n")

# Start with the exact greeting the phone call uses
initial_greeting = "Welcome to our restaurant! What would you like to order today?"
print(f"🤖 Bot: {initial_greeting}")
chat_history.append({"role": "assistant", "content": initial_greeting})

# The testing loop
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
            model="llama-3.1-8b-instant",  # Extreme low-latency model optimized for chat
            messages=chat_history,
            temperature=0.3
        )
        
        ai_response = response.choices[0].message.content.strip()
        
        # Clear line and print response
        print("\033[K🤖 Bot: " + ai_response)
        
        chat_history.append({"role": "assistant", "content": ai_response})

    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit()
    except Exception as e:
        print(f"\n❌ Error connecting to Groq: {e}")
        sys.exit()