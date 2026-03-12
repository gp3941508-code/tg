import asyncio
from telethon import TelegramClient

# --- CONFIGURATION ---
API_ID = 34403046  
API_HASH = 'b7efb741538c92732411867242b93a15' 
# ---------------------

async def make_session():
    print("--- Telegram Session Maker (Async Mode) ---")
    
    session_name = input("Enter session name (e.g., phone number): ").strip()
    
    if not session_name.endswith(".session"):
        full_name = session_name
    else:
        full_name = session_name.replace(".session", "")

    # Hum yahan client ko initialize kar rahe hain
    client = TelegramClient(full_name, API_ID, API_HASH)

    try:
        await client.start()
        
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"\n✅ Success! Logged in as: {me.first_name}")
            print(f"📂 Session file saved as: {full_name}.session")
            print("Ab aap is file ko bot ke 'sessions/' folder mein daal sakte hain.")
        else:
            print("\n❌ Login failed. Please try again.")

    except Exception as e:
        print(f"\n⚠️ Error: {e}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    # Naye Python versions (3.10+) ke liye sahi tarika
    try:
        asyncio.run(make_session())
    except KeyboardInterrupt:
        print("\nStopped by user.")