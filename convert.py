
__TCM_FILE_HASH__ = "4838259368"

import os
import asyncio
from tdata_reader import read_tdata
from telethon import TelegramClient
from telethon.sessions import StringSession


TDATA_DIR = "./tdata"
OUTPUT_DIR = "./sessions"


os.makedirs(OUTPUT_DIR, exist_ok=True)


async def convert_single_tdata(folder_name: str, tdata_path: str):
    """Read tdata and save it directly into a new .session file."""
    session_file_path = os.path.join(OUTPUT_DIR, f"{folder_name}.session")

    try:
        
        acc = read_tdata(tdata_path)

        
        string_session = acc.to_string_session()
        
        
        async with TelegramClient(
            StringSession(string_session),
            acc.api_id,
            acc.api_hash,
            device_model=acc.device_model or "PC 64bit",
            system_version=acc.system_version or "Windows 10",
            app_version=acc.app_version or "4.16.2"
        ) as temp_client:
            
            
            me = await temp_client.get_me()

            if me:
                
                file_client = TelegramClient(
                    session_file_path,
                    acc.api_id,
                    acc.api_hash
                )
                file_client.session.set_dc(
                    temp_client.session.dc_id,
                    temp_client.session.server_address,
                    temp_client.session.port
                )
                file_client.session.auth_key = temp_client.session.auth_key
                file_client.session.save()
                await file_client.disconnect()

                user_info = f"{me.first_name or ''} {me.last_name or ''}".strip()
                username = f"(@{me.username})" if me.username else ""
                print(f"✅ [SUCCESS] {folder_name} -> converted | User: {user_info} {username}")
            else:
                print(f"⚠️ [WARNING] {folder_name} -> invalid session.")

    except Exception as e:
        print(f"❌ [ERROR] Failed to convert {folder_name}: {e}")


async def main():
    if not os.path.exists(TDATA_DIR):
        print(f"❌ Directory '{TDATA_DIR}' was not found!")
        return

    items = sorted(os.listdir(TDATA_DIR))
    print("🚀 Starting tdata to Telethon session conversion...\n")

    for item in items:
        item_path = os.path.join(TDATA_DIR, item)

        if os.path.isdir(item_path):
            target_path = item_path
            if os.path.exists(os.path.join(item_path, "tdata")):
                target_path = os.path.join(item_path, "tdata")

            print(f"🔄 Processing: {item} ...")
            await convert_single_tdata(item, target_path)

    print("\n✨ Conversion complete! .session files were saved in the 'sessions' directory.")


if __name__ == "__main__":
    asyncio.run(main())
