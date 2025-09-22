import asyncio
import os
from telethon.sync import TelegramClient
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import User, Channel, Chat
from telethon.errors import (
    FloodWaitError,
    UserPrivacyRestrictedError,
    PeerFloodError,
    UserAlreadyParticipantError,
    ChatAdminRequiredError,
)
from telethon import utils
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


class TelegramGroupManager:
    def __init__(self, api_id, api_hash, phone_number):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.client = TelegramClient("telegram", api_id, api_hash)

    async def connect(self):
        """Connect to Telegram"""
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.send_code_request(self.phone_number)
            code = input("📨 Enter the code sent to you in Telegram: ")
            await self.client.sign_in(self.phone_number, code)
        logger.info("✅ Successfully connected to Telegram")

    async def get_user_groups(self):
        """Fetch all groups and channels, filtering out duplicates"""
        groups = []
        groups_map = {}  # Track groups by title to filter duplicates

        async for dialog in self.client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                group_info = {
                    "id": dialog.id,
                    "title": dialog.name,
                    "username": getattr(dialog.entity, "username", None),
                    "entity": dialog.entity,
                    "type": "📢 Channel" if dialog.is_channel else "👥 Group",
                    "participants_count": getattr(
                        dialog.entity, "participants_count", "N/A"
                    ),
                }

                # Use title as key to identify potential duplicates
                title_key = group_info["title"].lower().strip()

                # If we already have a group with this title
                if title_key in groups_map:
                    existing_group = groups_map[title_key]

                    # Prefer the group with a username over the one without
                    if group_info["username"] and not existing_group["username"]:
                        # Replace the existing group (without username) with this one (with username)
                        # Remove the existing group from the list
                        groups = [
                            g for g in groups if g["title"].lower().strip() != title_key
                        ]
                        groups_map[title_key] = group_info
                        groups.append(group_info)
                    elif not group_info["username"] and existing_group["username"]:
                        # Keep the existing group (with username), skip this one (without username)
                        pass
                    else:
                        # Both have usernames or both don't, keep the first one
                        if group_info not in groups:
                            groups.append(group_info)
                else:
                    # First time seeing this group title
                    groups_map[title_key] = group_info
                    groups.append(group_info)

        return groups

    async def prompt_group_selection(self):
        """Show groups and let user pick one"""
        groups = await self.get_user_groups()
        if not groups:
            logger.error("⚠️ No groups or channels found")
            return None

        print("\n" + "═" * 60)
        print("📂 YOUR GROUPS AND CHANNELS")
        print("═" * 60)
        for i, group in enumerate(groups, 1):
            username_display = f" (@{group['username']})" if group["username"] else ""
            print(f"{i:2d}. {group['title']}{username_display}")
            print(f"    {group['type']} | 👤 Members: {group['participants_count']}")
            print()

        while True:
            choice = input(
                f"👉 Select a group (1-{len(groups)}), or 'q' to quit: "
            ).strip()
            if choice.lower() == "q":
                return None
            try:
                choice_num = int(choice)
                if 1 <= choice_num <= len(groups):
                    selected = groups[choice_num - 1]
                    print(f"\n🎯 Selected: {selected['title']}")
                    return selected["entity"]
            except ValueError:
                pass
            print("❌ Invalid input, try again.")

    async def get_contacts_with_prefix(self, prefix, group_entity):
        """Fetch contacts and filter by prefix, excluding existing group members"""
        result = await self.client(GetContactsRequest(hash=0))
        users = result.users  # type: ignore

        # Fetch existing group participants
        existing = await self.client.get_participants(group_entity)
        existing_phones = {
            (
                "+" + p.phone
                if p.phone and not p.phone.startswith("+")
                else (p.phone or "")
            )
            for p in existing
        }

        filtered = []
        for c in users:
            if not isinstance(c, User):
                continue
            name = (c.first_name or "") + " " + (c.last_name or "")
            phone = c.phone or ""
            phone_fmt = "+" + phone if phone and not phone.startswith("+") else phone

            if (
                (name.lower().startswith(prefix.lower()) or phone.startswith(prefix))
                and phone_fmt
                and phone_fmt not in existing_phones
            ):
                filtered.append(phone_fmt)

        return filtered

    async def add_members_to_group(self, group_entity, phone_numbers, delay=5):
        """Add members to group"""
        successful, failed = [], []
        for i, phone_number in enumerate(phone_numbers):
            try:
                logger.info(f"➡️  Processing {i+1}/{len(phone_numbers)}: {phone_number}")
                try:
                    user_entity = await self.client.get_entity(phone_number)
                    if not isinstance(user_entity, User):
                        raise ValueError("Entity is not a user")
                except Exception as e:
                    logger.error(f"❌ Could not resolve {phone_number}: {e}")
                    failed.append({"phone": phone_number, "error": str(e)})
                    continue

                if isinstance(group_entity, Channel):
                    input_channel = utils.get_input_channel(group_entity)

                    if not input_channel:
                        logger.error(f"❌ Invalid input channel")
                        return

                    await self.client(
                        InviteToChannelRequest(
                            channel=input_channel,
                            users=[utils.get_input_user(user_entity)],
                        )
                    )
                elif isinstance(group_entity, Chat):
                    await self.client(
                        AddChatUserRequest(
                            chat_id=group_entity.id,
                            user_id=utils.get_input_user(user_entity),
                            fwd_limit=50,
                        )
                    )
                else:
                    raise ValueError("Unsupported group type")

                successful.append(phone_number)
                logger.info(f"✅ Added {phone_number}")
                await asyncio.sleep(delay)

            except UserAlreadyParticipantError:
                logger.info(f"ℹ️ {phone_number} is already a member")
                successful.append(phone_number)
            except UserPrivacyRestrictedError:
                failed.append({"phone": phone_number, "error": "Privacy restricted"})
            except FloodWaitError as e:
                logger.warning(f"⏳ Flood wait {e.seconds}s, retrying...")
                await asyncio.sleep(e.seconds)
                phone_numbers.insert(i, phone_number)
            except (PeerFloodError, ChatAdminRequiredError) as e:
                failed.append({"phone": phone_number, "error": str(e)})
                break
            except Exception as e:
                failed.append({"phone": phone_number, "error": str(e)})

        return successful, failed

    async def disconnect(self):
        if self.client.is_connected():
            self.client.disconnect()


async def batch_add_members():
    """Batch add members"""
    API_ID = os.getenv("TELEGRAM_API_ID")
    API_HASH = os.getenv("TELEGRAM_API_HASH")
    PHONE_NUMBER = os.getenv("TELEGRAM_PHONE_NUMBER")
    if not all([API_ID, API_HASH, PHONE_NUMBER]):
        logger.error("⚠️ Set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE_NUMBER")
        return

    manager = TelegramGroupManager(API_ID, API_HASH, PHONE_NUMBER)

    try:
        await manager.connect()
        group = await manager.prompt_group_selection()
        if not group:
            return

        prefix = input("🔍 Enter prefix to filter contacts (e.g. +2547, SW): ").strip()
        contacts = await manager.get_contacts_with_prefix(prefix, group)

        if not contacts:
            logger.warning("⚠️ No contacts matched the prefix")
            return

        print(
            f"\n✨ Found {len(contacts)} matching contacts (excluding existing members)."
        )

        # Ask for start index
        max_index = len(contacts) - 1
        while True:
            try:
                start_index = int(input(f"📍 Enter start index (0 - {max_index}): "))
                if 0 <= start_index <= max_index:
                    break
            except ValueError:
                pass
            print("❌ Invalid input, try again.")

        # Ask for amount
        max_amount = len(contacts) - start_index
        while True:
            try:
                amount = int(
                    input(
                        f"🔢 Enter how many contacts to process (max: {max_amount}): "
                    )
                )
                if 1 <= amount <= max_amount:
                    break
            except ValueError:
                pass
            print("❌ Invalid input, try again.")

        # Slice contacts
        contacts = contacts[start_index : start_index + amount]

        confirm = (
            input(f"⚡ Proceed with adding {len(contacts)} contacts? (y/n): ")
            .strip()
            .lower()
        )
        if confirm != "y":
            return

        CHUNK_SIZE = int(os.getenv("TELEGRAM_CHUNK_SIZE", 10))
        CHUNK_DELAY = int(os.getenv("TELEGRAM_CHUNK_DELAY", 60))
        REQUEST_DELAY = int(os.getenv("TELEGRAM_REQUEST_DELAY", 3))

        all_successful, all_failed = [], []
        for i in range(0, len(contacts), CHUNK_SIZE):
            chunk = contacts[i : i + CHUNK_SIZE]
            success, fail = await manager.add_members_to_group(
                group, chunk, delay=REQUEST_DELAY
            )
            all_successful.extend(success)
            all_failed.extend(fail)
            if i + CHUNK_SIZE < len(contacts):
                await asyncio.sleep(CHUNK_DELAY)

        print("\n" + "═" * 60)
        print("🎉 BATCH PROCESS COMPLETED 🎉")
        print(f"✅ Added: {len(all_successful)} | ❌ Failed: {len(all_failed)}")
        print("═" * 60 + "\n")

        # 🚀 Branding footer
        print("💡 Made by Karume-lab")
        print("🌍 Portfolio: https://karume.vercel.app")
        print("🐙 GitHub:   https://github.com/Karume-lab")
        print("✉️  Email:   mailto:danielkarume.work@gmail.com\n")
        print(
            "👉 Reach out for collaborations, freelance projects, or tech discussions!\n"
        )

    finally:
        await manager.disconnect()


if __name__ == "__main__":
    print("🚀 Telegram Group Member Adder")
    print("=" * 50)
    asyncio.run(batch_add_members())
