import os
import logging
import nest_asyncio
import asyncio
import json
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
)
from telegram.ext.filters import ChatType, COMMAND, TEXT
from typing import Optional

# Telethon imports
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

# Define conversation states
# We now add VERIFY_CODE and VERIFY_2FA to handle the sign-in flow
(MAIN_MENU, SET_CREDENTIALS, SET_FORWARDING, REMOVE_RULE, VERIFY_CODE, VERIFY_2FA) = range(6)

# Apply nest_asyncio patch
nest_asyncio.apply()

# ---------------------------
# Logging configuration
# ---------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
# File-Based Configuration Helpers
# ---------------------------
DATA_FOLDER = "data"
if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

def get_user_config_path(user_id: int) -> str:
    return os.path.join(DATA_FOLDER, f"{user_id}.json")

def load_user_config(user_id: int) -> dict:
    file_path = get_user_config_path(user_id)
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Error loading config for user %s: %s", user_id, e)
            return {}
    return {}

def save_user_config(user_id: int, config: dict) -> None:
    file_path = get_user_config_path(user_id)
    try:
        with open(file_path, "w") as f:
            json.dump(config, f)
    except Exception as e:
        logger.error("Error saving config for user %s: %s", user_id, e)

# ---------------------------
# Flask web server for Replit
# ---------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive!", 200

def run_webserver():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

def start_webserver():
    server = Thread(target=run_webserver)
    server.start()

# ---------------------------
# Helper: Send Main Menu
# ---------------------------
async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user is None:
        logger.error("No user information available.")
        return ConversationHandler.END

    config = load_user_config(user.id)
    creds_set = ("api_id" in config and "api_hash" in config and "phone" in config)
    rules = config.get("forwarding_rules", {})

    text = "What do you want to do?\n\n"
    if creds_set:
        text += "Your Telethon credentials are set.\n"
    else:
        text += "You have not set your Telethon credentials yet.\n"

    if rules:
        text += "Your forwarding rules:\n"
        for src, dests in rules.items():
            text += f"Source: {src}\n  Destinations: {dests}\n"
    else:
        text += "You have no forwarding rules set up.\n"

    # New button "List My Chats" added here
    keyboard = [
        [InlineKeyboardButton("Set Telethon Credentials", callback_data="set_creds")],
        [InlineKeyboardButton("Add Forwarding Rule", callback_data="set_forward")],
        [InlineKeyboardButton("Remove Forwarding Rule", callback_data="remove_rule")],
        [InlineKeyboardButton("List My Chats", callback_data="list_chats")],
        [InlineKeyboardButton("Exit", callback_data="menu_exit")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    return MAIN_MENU

# ---------------------------
# /start Command Handler
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user or (update.message.from_user if update.message else None)
    if user is None:
        logger.error("User information not available. Cannot proceed.")
        return ConversationHandler.END
    logger.info("User %s started the bot.", user.id)
    return await send_main_menu(update, context)

# ---------------------------
# Menu Callback Handler
# ---------------------------
async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        return await send_main_menu(update, context)
    await query.answer()
    choice = query.data
    if choice == "set_creds":
        await query.edit_message_text(
            "Please send your API credentials in the following format:\n\nAPI_ID,API_HASH,PHONE_NUMBER"
        )
        return SET_CREDENTIALS
    elif choice == "set_forward":
        await query.edit_message_text(
            "Please send the forwarding rule in the format:\n\nSOURCE_CHAT_ID,DEST_CHAT_ID"
        )
        return SET_FORWARDING
    elif choice == "remove_rule":
        await query.edit_message_text(
            "Please send the source chat ID of the rule you want to remove:"
        )
        return REMOVE_RULE
    elif choice == "list_chats":
        return await list_chats(update, context)
    elif choice == "menu_exit":
        await query.edit_message_text("Goodbye!")
        return ConversationHandler.END
    return MAIN_MENU

# ---------------------------
# New Function: List My Chats
# ---------------------------
async def list_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Initialize user_data if None
    if context.user_data is None:
        context.user_data = {}

    client: Optional[TelegramClient] = context.user_data.get('telethon_client')

    async def send_error_message(message: str) -> int:
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(message)
        elif update.callback_query:
            await update.callback_query.answer(text=message, show_alert=True)
        elif update.message:
            await update.message.reply_text(message)
        return MAIN_MENU

    if client is None:
        return await send_error_message("Telethon client not found. Please set your credentials first.")

    if not client.is_connected():
        await client.connect()

    if not await client.is_user_authorized():
        return await send_error_message("Telethon client is not authorized. Please set your credentials again.")

    me = await client.get_me()
    print(f"HELLO WORLD, {me}")

    try:
        result = "Your Chats:\n"
        async for dialog in client.iter_dialogs():
            result += f"{dialog.name} : {dialog.id}\n"

        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(result)
        elif update.message:
            await update.message.reply_text(result)
        else:
            logger.warning("No valid message object to reply with chat list.")

        return await send_main_menu(update, context)
    except Exception as e:
        logger.error("Error listing chats: %s", e)
        return await send_error_message("Error retrieving chat list.")

# ---------------------------
# Telethon Credentials Handler
# ---------------------------
async def set_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        if update.effective_message:
            await update.effective_message.reply_text(
                "No text received. Please send your credentials as:\nAPI_ID,API_HASH,PHONE_NUMBER"
            )
        return SET_CREDENTIALS

    if context.user_data is None:
        context.user_data = {}

    text = update.message.text.strip()
    try:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 3:
            raise ValueError("Incorrect format")
        api_id = int(parts[0])
        api_hash = parts[1]
        phone = parts[2]

        user = update.effective_user
        if user is None:
            await update.message.reply_text("User information missing. Please try again.")
            return SET_CREDENTIALS

        user_id = user.id
        config = load_user_config(user_id)
        if config.get("api_id") and config.get("api_hash"):
            if config["api_id"] != api_id or config["api_hash"] != api_hash:
                await update.message.reply_text("The credentials you provided do not match your stored credentials.")
                return SET_CREDENTIALS
        else:
            config["api_id"] = api_id
            config["api_hash"] = api_hash
            config["phone"] = phone
            if "forwarding_rules" not in config:
                config["forwarding_rules"] = {}
            save_user_config(user_id, config)

        session_name = f"session_{user_id}"
        client = TelegramClient(session_name, api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await update.message.reply_text("Sending verification code...")
            await client.send_code_request(phone)
            context.user_data['temp_client'] = client
            context.user_data['phone'] = phone
            await update.message.reply_text("Please check your Telegram app for the verification code and send it here.")
            return VERIFY_CODE

        me = await client.get_me()
        if me is None:
            await update.message.reply_text("Failed to retrieve your account info. Please check your credentials.")
            return SET_CREDENTIALS

        context.user_data['telethon_client'] = client
        await update.message.reply_text("Telethon credentials set and client started successfully!")
        return await send_main_menu(update, context)
    except Exception as e:
        logger.error("Error setting credentials: %s", e)
        await update.message.reply_text("Error setting credentials. Please ensure the format is:\nAPI_ID,API_HASH,PHONE_NUMBER")
        return SET_CREDENTIALS

# ---------------------------
# Verify Code Handler
# ---------------------------
async def verify_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        if update.effective_message:
            await update.effective_message.reply_text("Please send the verification code.")
        return VERIFY_CODE

    if context.user_data is None:
        if update.effective_message:
            await update.effective_message.reply_text("Session expired. Please start over with /start")
        return await send_main_menu(update, context)

    try:
        code = update.message.text.strip()
        client: Optional[TelegramClient] = context.user_data.get('temp_client')
        phone = context.user_data.get('phone')
        if not client or not phone:
            await update.message.reply_text("Session expired. Please start over with /start")
            return await send_main_menu(update, context)
        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            await update.message.reply_text("Two-step verification is enabled. Please send your password.")
            return VERIFY_2FA

        context.user_data['telethon_client'] = client
        context.user_data.pop('temp_client', None)
        context.user_data.pop('phone', None)
        await update.message.reply_text("Successfully signed in!")
        return await send_main_menu(update, context)
    except Exception as e:
        logger.error("Error during verification: %s", e)
        await update.message.reply_text("Error during verification. Please try again or start over with /start")
        return VERIFY_CODE

# ---------------------------
# Verify 2FA Handler
# ---------------------------
async def verify_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        if update.effective_message:
            await update.effective_message.reply_text("Please send your two-step verification password.")
        return VERIFY_2FA

    if context.user_data is None:
        if update.effective_message:
            await update.effective_message.reply_text("Session expired. Please start over with /start")
        return await send_main_menu(update, context)

    try:
        password = update.message.text.strip()
        client: Optional[TelegramClient] = context.user_data.get('temp_client')
        phone = context.user_data.get('phone')
        if not client or not phone:
            await update.message.reply_text("Session expired. Please start over with /start")
            return await send_main_menu(update, context)

        await client.sign_in(password=password)
        context.user_data['telethon_client'] = client
        context.user_data.pop('temp_client', None)
        context.user_data.pop('phone', None)
        await update.message.reply_text("Successfully signed in with 2FA!")
        return await send_main_menu(update, context)
    except Exception as e:
        logger.error("Error during 2FA verification: %s", e)
        await update.message.reply_text("Error during 2FA verification. Please try again or start over with /start")
        return VERIFY_2FA

# ---------------------------
# Telethon Forwarding Setup Handler
# ---------------------------
async def set_forwarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        if update.effective_message:
            await update.effective_message.reply_text("No text received. Please send the rule as:\nSOURCE_CHAT_ID,DEST_CHAT_ID")
        return SET_FORWARDING

    if context.user_data is None:
        context.user_data = {}

    text = update.message.text.strip()
    try:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 2:
            raise ValueError("Incorrect format")
        source_id = int(parts[0])
        dest_id = int(parts[1])

        user = update.effective_user
        if user is None:
            await update.message.reply_text("User information missing. Please try again.")
            return SET_FORWARDING

        config = load_user_config(user.id)
        if "forwarding_rules" not in config:
            config["forwarding_rules"] = {}
        src_key = str(source_id)
        if src_key in config["forwarding_rules"]:
            if dest_id not in config["forwarding_rules"][src_key]:
                config["forwarding_rules"][src_key].append(dest_id)
        else:
            config["forwarding_rules"][src_key] = [dest_id]
        save_user_config(user.id, config)

        client: Optional[TelegramClient] = context.user_data.get('telethon_client')
        if client is None:
            await update.message.reply_text("Telethon client not found. Please set your credentials first.")
            return await send_main_menu(update, context)

        @client.on(events.NewMessage(chats=source_id))
        async def forward_handler(event):
            try:
                await client.forward_messages(dest_id, event.message)
                logger.info("Forwarded message from %s to %s", source_id, dest_id)
            except Exception as e:
                logger.error("Failed to forward message: %s", e)

        await update.message.reply_text("Forwarding rule set up successfully! New messages from the source chat will be forwarded.")
        return await send_main_menu(update, context)
    except ValueError:
        await update.message.reply_text("Invalid format. Please ensure the format is:\nSOURCE_CHAT_ID,DEST_CHAT_ID")
        return SET_FORWARDING
    except Exception as e:
        logger.error("Error setting forwarding: %s", e)
        await update.message.reply_text("Error setting forwarding. Please ensure the format is:\nSOURCE_CHAT_ID,DEST_CHAT_ID")
        return SET_FORWARDING

# ---------------------------
# Remove Forwarding Rule Handler
# ---------------------------
async def remove_rule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        if update.effective_message:
            await update.effective_message.reply_text("No text received. Please send the source chat ID to remove:")
        return REMOVE_RULE

    text = update.message.text.strip()
    try:
        source_id = int(text)
        user = update.effective_user
        if user is None:
            await update.message.reply_text("User information missing. Please try again.")
            return REMOVE_RULE

        config = load_user_config(user.id)
        rules = config.get("forwarding_rules", {})
        src_key = str(source_id)
        if src_key not in rules:
            await update.message.reply_text("No forwarding rule exists for that source chat ID.")
            return REMOVE_RULE

        dests = rules[src_key]
        await update.message.reply_text(f"Current destinations for source {source_id}: {dests}\nPlease send the destination chat ID to remove:")
        if context.user_data is None:
            context.user_data = {}
        context.user_data['remove_source'] = src_key
        return REMOVE_RULE + 10
    except Exception as e:
        logger.error("Error processing removal: %s", e)
        if update.message:
            await update.message.reply_text("Error processing your request. Please try again.")
        return REMOVE_RULE

# Substate: Remove specific destination
async def remove_destination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        if update.effective_message:
            await update.effective_message.reply_text("No text received. Please send the destination chat ID to remove:")
        return REMOVE_RULE + 10

    text = update.message.text.strip()
    try:
        dest_id = int(text)
        user = update.effective_user
        if user is None:
            if update.message:
                await update.message.reply_text("User information missing. Please try again.")
            return await send_main_menu(update, context)

        config = load_user_config(user.id)
        if context.user_data is None:
            context.user_data = {}
        src_key = context.user_data.get('remove_source')
        if not src_key or src_key not in config.get("forwarding_rules", {}):
            await update.message.reply_text("No valid source found. Please try again.")
            return await send_main_menu(update, context)

        dests = config["forwarding_rules"][src_key]
        if dest_id not in dests:
            await update.message.reply_text("That destination is not set for this source. Please try again.")
            return REMOVE_RULE + 10

        dests.remove(dest_id)
        if not dests:
            del config["forwarding_rules"][src_key]
        save_user_config(user.id, config)
        await update.message.reply_text("Destination removed successfully.")
        return await send_main_menu(update, context)
    except Exception as e:
        logger.error("Error removing destination: %s", e)
        if update.message:
            await update.message.reply_text("Error processing removal. Please try again.")
        return REMOVE_RULE + 10

# ---------------------------
# Conversation Handler Setup
# ---------------------------
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        MAIN_MENU: [CallbackQueryHandler(menu_choice)],
        SET_CREDENTIALS: [MessageHandler(TEXT & ~COMMAND, set_credentials)],
        SET_FORWARDING: [MessageHandler(TEXT & ~COMMAND, set_forwarding)],
        REMOVE_RULE: [MessageHandler(TEXT & ~COMMAND, remove_rule)],
        REMOVE_RULE + 10: [MessageHandler(TEXT & ~COMMAND, remove_destination)],
        VERIFY_CODE: [MessageHandler(TEXT & ~COMMAND, verify_code)],
        VERIFY_2FA: [MessageHandler(TEXT & ~COMMAND, verify_2fa)],
    },
    fallbacks=[CommandHandler("cancel", start)],
)

# ---------------------------
# Main Function: Start Flask and the Bot
# ---------------------------
async def main():
    try:
        start_webserver()  # Start the Flask web server in a separate thread
        token = os.environ.get("TOKEN")
        if not token:
            logger.error("No TOKEN environment variable set!")
            return
        app_bot = Application.builder().token(token).build()
        app_bot.add_handler(conv_handler)
        logger.info("Starting bot polling...")
        app_bot.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Error in main function: {e}")
        raise

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot stopped due to error: {e}")
