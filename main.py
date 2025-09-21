import asyncio
import os
from telethon.sync import TelegramClient
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import AddChatUserRequest
from telethon.tl.types import (
    User,
    Channel,
    Chat,
)
from telethon.errors import FloodWaitError, UserPrivacyRestrictedError, PeerFloodError
from telethon.errors import UserAlreadyParticipantError, ChatAdminRequiredError
from telethon import utils
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TelegramGroupManager:
    def __init__(self, api_id, api_hash, phone_number):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.client = TelegramClient("session", api_id, api_hash)

    async def connect(self):
        """Connect to Telegram and authenticate"""
        await self.client.connect()

        if not await self.client.is_user_authorized():
            await self.client.send_code_request(self.phone_number)
            code = input("Enter the code you received: ")
            await self.client.sign_in(self.phone_number, code)

        logger.info("Successfully connected to Telegram")

    async def get_user_groups(self):
        """Fetch all groups and channels the user is part of"""
        groups = []

        logger.info("Fetching your groups and channels...")
        async for dialog in self.client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                group_info = {
                    "id": dialog.id,
                    "title": dialog.name,
                    "username": getattr(dialog.entity, "username", None),
                    "entity": dialog.entity,
                    "type": "Channel" if dialog.is_channel else "Group",
                    "participants_count": getattr(
                        dialog.entity, "participants_count", "N/A"
                    ),
                }
                groups.append(group_info)

        return groups

    async def prompt_group_selection(self):
        """Display all groups and let user select one"""
        groups = await self.get_user_groups()

        if not groups:
            logger.error("No groups or channels found in your account")
            return None

        print("\n" + "=" * 60)
        print("YOUR GROUPS AND CHANNELS:")
        print("=" * 60)

        for i, group in enumerate(groups, 1):
            username_display = f" (@{group['username']})" if group["username"] else ""
            print(f"{i:2d}. {group['title']}{username_display}")
            print(f"    Type: {group['type']}, Members: {group['participants_count']}")
            print()

        print("=" * 60)

        while True:
            try:
                choice = input(
                    f"Select a group (1-{len(groups)}), or 'q' to quit: "
                ).strip()

                if choice.lower() == "q":
                    return None

                choice_num = int(choice)
                if 1 <= choice_num <= len(groups):
                    selected_group = groups[choice_num - 1]
                    print(f"\nSelected: {selected_group['title']}")
                    return selected_group["entity"]
                else:
                    print(f"Please enter a number between 1 and {len(groups)}")

            except ValueError:
                print("Please enter a valid number or 'q' to quit")

    async def add_members_to_group(self, group_entity, phone_numbers, delay=5):
        """
        Add multiple phone numbers to a group with error handling and delays

        Args:
            group_entity: The group entity object
            phone_numbers: List of phone numbers (with country code)
            delay: Delay between requests in seconds
        """
        if group_entity is None:
            logger.error("Group entity is None, cannot add members")
            return [], [
                {"phone": phone, "error": "Group not found"} for phone in phone_numbers
            ]

        successful_adds = []
        failed_adds = []

        for i, phone_number in enumerate(phone_numbers):
            try:
                logger.info(f"Processing {i+1}/{len(phone_numbers)}: {phone_number}")

                # Get user entity from phone number
                try:
                    user_entity = await self.client.get_entity(phone_number)

                    # Ensure we have a User entity, not Channel or Chat
                    if not isinstance(user_entity, User):
                        logger.error(f"Entity for {phone_number} is not a user")
                        failed_adds.append(
                            {"phone": phone_number, "error": "Entity is not a user"}
                        )
                        continue

                except Exception as e:
                    logger.error(f"Could not find user with phone {phone_number}: {e}")
                    failed_adds.append(
                        {"phone": phone_number, "error": f"User not found: {e}"}
                    )
                    continue

                # Add user to group based on group type
                if isinstance(group_entity, Channel):
                    # For channels/supergroups
                    input_channel = utils.get_input_channel(group_entity)
                    if not input_channel:
                        logger.error(
                            f"Failed to get valid input channel for group: {getattr(group_entity, 'title', 'Unknown')}"
                        )
                        failed_adds.append(
                            {
                                "phone": phone_number,
                                "error": "Invalid channel reference - cannot add members",
                            }
                        )
                        continue

                    input_user = utils.get_input_user(user_entity)
                    if not input_user:
                        logger.error(
                            f"Failed to get valid input user for phone: {phone_number}"
                        )
                        failed_adds.append(
                            {
                                "phone": phone_number,
                                "error": "Invalid user reference - cannot add to channel",
                            }
                        )
                        continue

                    await self.client(
                        InviteToChannelRequest(
                            channel=input_channel, users=[input_user]
                        )
                    )
                elif isinstance(group_entity, Chat):
                    # For regular groups
                    input_user = utils.get_input_user(user_entity)

                    await self.client(
                        AddChatUserRequest(
                            chat_id=group_entity.id, user_id=input_user, fwd_limit=50
                        )
                    )
                else:
                    logger.error(f"Unsupported group type: {type(group_entity)}")
                    failed_adds.append(
                        {"phone": phone_number, "error": "Unsupported group type"}
                    )
                    continue

                successful_adds.append(phone_number)
                logger.info(f"Successfully added {phone_number}")

                # Add delay to avoid rate limiting
                await asyncio.sleep(delay)

            except FloodWaitError as e:
                wait_time = e.seconds
                logger.warning(f"Rate limited. Waiting {wait_time} seconds...")
                await asyncio.sleep(wait_time)
                # Retry the same number
                phone_numbers.insert(i, phone_number)

            except UserAlreadyParticipantError:
                logger.info(f"{phone_number} is already in the group")
                successful_adds.append(phone_number)

            except UserPrivacyRestrictedError:
                logger.warning(f"{phone_number} has privacy restrictions")
                failed_adds.append(
                    {"phone": phone_number, "error": "Privacy restrictions"}
                )

            except PeerFloodError:
                logger.error(
                    "Peer flood error. Too many requests. Wait before continuing."
                )
                failed_adds.append(
                    {"phone": phone_number, "error": "Peer flood - too many requests"}
                )
                break

            except ChatAdminRequiredError:
                logger.error("Admin rights required to add members")
                failed_adds.append(
                    {"phone": phone_number, "error": "Admin rights required"}
                )
                break

            except Exception as e:
                logger.error(f"Unexpected error adding {phone_number}: {e}")
                failed_adds.append({"phone": phone_number, "error": str(e)})

        return successful_adds, failed_adds

    async def disconnect(self):
        """Disconnect from Telegram"""
        if self.client.is_connected():
            self.client.disconnect()


def read_phone_numbers_from_directory(directory_path="phone-numbers"):
    """
    Read phone numbers from all text files in the specified directory.
    Each file should contain phone numbers, one per line.

    Args:
        directory_path: Path to the directory containing phone number files

    Returns:
        List of phone numbers from all files
    """
    phone_numbers = []

    # Check if directory exists
    if not os.path.exists(directory_path):
        logger.error(f"Directory '{directory_path}' does not exist")
        return phone_numbers

    # Get all text files in the directory
    text_files = [f for f in os.listdir(directory_path) if f.endswith(".txt")]

    if not text_files:
        logger.error(f"No text files found in '{directory_path}'")
        return phone_numbers

    # Read phone numbers from each file
    for file_name in text_files:
        file_path = os.path.join(directory_path, file_name)
        try:
            with open(file_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith(
                        "#"
                    ):  # Skip empty lines and comments
                        phone_numbers.append(line)
            logger.info(f"Read phone numbers from {file_name}")
        except Exception as e:
            logger.error(f"Error reading file {file_name}: {e}")

    logger.info(f"Total phone numbers loaded: {len(phone_numbers)}")
    return phone_numbers


async def batch_add_members():
    """Add members in chunks to avoid rate limiting (Batch Processing Mode)"""

    # Get API credentials from environment variables
    API_ID = os.getenv("TELEGRAM_API_ID")
    API_HASH = os.getenv("TELEGRAM_API_HASH")
    PHONE_NUMBER = os.getenv("TELEGRAM_PHONE_NUMBER")

    # Validate that environment variables are set
    if not all([API_ID, API_HASH, PHONE_NUMBER]):
        logger.error(
            "Please set TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE_NUMBER in your .env file"
        )
        return

    # Read phone numbers from the phone_numbers directory
    PHONE_NUMBERS = read_phone_numbers_from_directory()

    if not PHONE_NUMBERS:
        logger.error("No phone numbers found to process")
        return

    # Get batch processing settings from environment variables
    CHUNK_SIZE = int(os.getenv("TELEGRAM_CHUNK_SIZE", 10))
    CHUNK_DELAY = int(os.getenv("TELEGRAM_CHUNK_DELAY", 60))
    REQUEST_DELAY = int(os.getenv("TELEGRAM_REQUEST_DELAY", 3))

    manager = TelegramGroupManager(API_ID, API_HASH, PHONE_NUMBER)

    try:
        # Connect to Telegram
        await manager.connect()

        # Let user select a group from their list
        group = await manager.prompt_group_selection()
        if not group:
            logger.info("No group selected. Exiting.")
            return

        group_title = getattr(
            group, "title", getattr(group, "username", "Unknown Group")
        )
        logger.info(f"Selected group: {group_title}")

        # Show batch configuration
        print(f"\n{'='*50}")
        print("BATCH PROCESSING CONFIGURATION:")
        print(f"{'='*50}")
        print(f"Total phone numbers: {len(PHONE_NUMBERS)}")
        print(f"Chunk size: {CHUNK_SIZE}")
        print(f"Delay between requests: {REQUEST_DELAY} seconds")
        print(f"Delay between chunks: {CHUNK_DELAY} seconds")
        print(
            f"Estimated chunks: {(len(PHONE_NUMBERS) + CHUNK_SIZE - 1) // CHUNK_SIZE}"
        )
        print(f"{'='*50}")

        # Confirm with user
        confirm = (
            input(f"\nStart batch processing for '{group_title}'? (y/n): ")
            .strip()
            .lower()
        )
        if confirm != "y":
            logger.info("Operation cancelled.")
            return

        # Process in chunks
        all_successful = []
        all_failed = []

        for i in range(0, len(PHONE_NUMBERS), CHUNK_SIZE):
            chunk = PHONE_NUMBERS[i : i + CHUNK_SIZE]
            chunk_num = (i // CHUNK_SIZE) + 1
            total_chunks = (len(PHONE_NUMBERS) + CHUNK_SIZE - 1) // CHUNK_SIZE

            logger.info(f"\n=== Processing Chunk {chunk_num}/{total_chunks} ===")
            logger.info(f"Chunk size: {len(chunk)} phone numbers")

            successful, failed = await manager.add_members_to_group(
                group, chunk, delay=REQUEST_DELAY
            )

            all_successful.extend(successful)
            all_failed.extend(failed)

            logger.info(
                f"Chunk {chunk_num} completed: {len(successful)} successful, {len(failed)} failed"
            )

            # Wait between chunks (except for the last chunk)
            if i + CHUNK_SIZE < len(PHONE_NUMBERS):
                logger.info(f"Waiting {CHUNK_DELAY} seconds before next chunk...")
                await asyncio.sleep(CHUNK_DELAY)

        # Print final results
        logger.info(f"\n{'='*50}")
        logger.info("BATCH PROCESSING COMPLETED")
        logger.info(f"{'='*50}")
        logger.info(f"Total successful: {len(all_successful)}")
        logger.info(f"Total failed: {len(all_failed)}")

        if all_successful:
            logger.info(f"\nSuccessfully added members:")
            for phone in all_successful:
                logger.info(f"  ✓ {phone}")

        if all_failed:
            logger.info(f"\nFailed to add members:")
            for item in all_failed:
                logger.info(f"  ✗ {item['phone']}: {item['error']}")

    except Exception as e:
        logger.error(f"Batch processing error: {e}")

    finally:
        await manager.disconnect()


if __name__ == "__main__":
    print("Telegram Group Member Adder - Batch Processing Mode")
    print("=" * 50)
    asyncio.run(batch_add_members())
