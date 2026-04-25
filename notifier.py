import asyncio
import requests
import os

# There's a BOT_TOKEN_SERVICE and CHAT_ID_SERVICE. Can be used in the future.
# --- Your Secondary Code (Synchronous) ---
def send_telegram_message(message):
    bot_token_contact = os.getenv("BOT_TOKEN_CONTACT")
    chat_id_contact = os.getenv("CHAT_ID_CONTACT")

    # Safety check to prevent crashing if env vars are missing
    if not bot_token_contact or not chat_id_contact:
        print("Error: BOT_TOKEN or CHAT_ID not found.")
        return

    url = f"https://api.telegram.org/bot{bot_token_contact}/sendMessage"
    payload = {
        "chat_id": chat_id_contact,
        "text": message
    }

    try:
        # This is a blocking call, but we will handle it safely below
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Failed to send notification: {response.text}")
    except Exception as e:
        print(f"Error connecting to Telegram: {e}")

async def main():

    await asyncio.to_thread(send_telegram_message, "🚀 Script Started: Heavy Task Analysis")

if __name__ == "__main__":

    asyncio.run(main())