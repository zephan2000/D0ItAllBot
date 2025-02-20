import os
import logging
import asyncio
import json
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ---------------------------
# Logging configuration
# ---------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
# File-Based Storage Helpers
# ---------------------------
DATA_FOLDER = "data"
if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)


def get_user_file_path(user_id: int) -> str:
    return os.path.join(DATA_FOLDER, f"{user_id}.json")


def load_user_rules(user_id: int) -> dict:
    file_path = get_user_file_path(user_id)
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            try:
                return json.load(f)
            except Exception as e:
                logger.error("Error loading rules for user %s: %s", user_id, e)
                return {}
    return {}


def save_user_rules(user_id: int, rules: dict) -> None:
    file_path = get_user_file_path(user_id)
    with open(file_path, "w") as f:
        json.dump(rules, f)


def get_all_user_rules() -> dict:
    """Load and return all forwarding rules from all user files."""
    all_rules = {}
    for filename in os.listdir(DATA_FOLDER):
        if filename.endswith(".json"):
            try:
                user_id = int(filename.replace(".json", ""))
                with open(os.path.join(DATA_FOLDER, filename), "r") as f:
                    rules = json.load(f)
                all_rules[user_id] = rules
            except Exception as e:
                logger.error("Error loading file %s: %s", filename, e)
    return all_rules


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
# Conversation States
# ---------------------------
MAIN_MENU, ADD_SOURCE, ADD_DEST, REMOVE_SOURCE, REMOVE_DEST = range(5)


# ---------------------------
# Helper: Send Main Menu
# ---------------------------
async def send_main_menu(update: Update,
                         context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [
            InlineKeyboardButton("Add Forwarding Rule",
                                 callback_data="menu_add")
        ],
        [
            InlineKeyboardButton("Remove Forwarding Rule",
                                 callback_data="menu_remove")
        ],
        [
            InlineKeyboardButton("List Forwarding Rules",
                                 callback_data="menu_list")
        ],
        [InlineKeyboardButton("Exit", callback_data="menu_exit")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text("What do you want to do?",
                                        reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            "What do you want to do?", reply_markup=reply_markup)
    return MAIN_MENU


# ---------------------------
# /start Command Handler
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user is None:
        if update.message and update.message.from_user:
            user = update.message.from_user
        else:
            logger.error("User information is not available. Cannot proceed.")
            return ConversationHandler.END
    user_id = user.id
    logger.info("User %s started the bot.", user_id)
    return await send_main_menu(update, context)


# ---------------------------
# Updated Menu Callback Handler
# ---------------------------
async def menu_choice(update: Update,
                      context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is not None:
        await query.answer()
        choice = query.data
        if choice == "menu_add":
            await query.edit_message_text(
                "Please send me the **source chat ID** (the group to pull messages from):"
            )
            return ADD_SOURCE
        elif choice == "menu_remove":
            await query.edit_message_text(
                "Please send me the **source chat ID** from which you want to remove a destination:"
            )
            return REMOVE_SOURCE
        elif choice == "menu_list":
            user = update.effective_user or (
                update.message.from_user
                if update.message and update.message.from_user else None)
            if user is None:
                logger.error(
                    "User information not available in menu_choice callback.")
                await query.edit_message_text(
                    "User information not available. Please restart with /start."
                )
                return ConversationHandler.END
            user_id = user.id
            rules = load_user_rules(user_id)
            if not rules:
                text = "You don't have any forwarding rules set up yet."
            else:
                text = "Your forwarding rules:\n"
                for src, dest_list in rules.items():
                    text += f"Source: {src}\n  Destinations: {dest_list}\n"
            await query.edit_message_text(text)
            await asyncio.sleep(2)
            return await send_main_menu(update, context)
        elif choice == "menu_exit":
            await query.edit_message_text("Goodbye!")
            return ConversationHandler.END
        return MAIN_MENU
    elif update.message is not None:
        user = update.effective_user or (
            update.message.from_user
            if update.message and update.message.from_user else None)
        if user is None:
            logger.error(
                "User information not available in menu_choice message.")
            await update.message.reply_text(
                "User information not available. Please restart with /start.")
            return ConversationHandler.END
        return await send_main_menu(update, context)
    return MAIN_MENU


# ---------------------------
# Add Rule: Handle Source Chat ID
# ---------------------------
async def add_source(update: Update,
                     context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    if message is None or not message.text:
        logger.error("No text message provided in add_source state.")
        if message is not None:
            await message.reply_text(
                "No text message received. Please restart with /start.")
        return ConversationHandler.END

    # Ensure context.user_data is not None.
    if context.user_data is None:
        context.user_data = {}

    text = message.text.strip()
    try:
        source_chat_id = int(text)
        await context.bot.get_chat(source_chat_id)
        context.user_data['add_source'] = source_chat_id
        await message.reply_text(
            "Source chat ID is valid. Now, please send me the **destination chat ID** (the group where messages will be forwarded):"
        )
        return ADD_DEST
    except Exception as e:
        logger.error("Invalid source chat id: %s", e)
        await message.reply_text(
            "The source chat ID appears invalid. Please enter a valid chat ID:"
        )
        return ADD_SOURCE


# ---------------------------
# Add Rule: Handle Destination Chat ID
# ---------------------------
async def add_dest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    if message is None or not message.text:
        logger.error("No text message provided in add_dest state.")
        if message is not None:
            await message.reply_text(
                "No text message received. Please restart with /start.")
        return ConversationHandler.END
    text = message.text.strip()
    try:
        dest_chat_id = int(text)
        await context.bot.get_chat(dest_chat_id)
        if context.user_data is None:
            logger.error("context.user_data is None in add_dest.")
            await message.reply_text(
                "User context data not available. Please restart with /start.")
            return ConversationHandler.END
        source_chat_id = context.user_data.get('add_source')
        if source_chat_id is None:
            logger.error("Source chat ID not set in user_data.")
            await message.reply_text(
                "Source chat ID not found. Please restart with /start.")
            return ConversationHandler.END
        user = update.effective_user or (update.message.from_user
                                         if update.message else None)
        if user is None:
            logger.error("User information not available in add_dest.")
            await message.reply_text(
                "User information not available. Please restart with /start.")
            return ConversationHandler.END
        user_id = user.id
        rules = load_user_rules(user_id)
        src_key = str(source_chat_id)
        if src_key in rules:
            if dest_chat_id not in rules[src_key]:
                rules[src_key].append(dest_chat_id)
        else:
            rules[src_key] = [dest_chat_id]
        save_user_rules(user_id, rules)
        await message.reply_text(
            f"Rule updated:\nSource: {source_chat_id}\nDestination(s): {rules[src_key]}"
        )
        keyboard = [[
            InlineKeyboardButton("Yes", callback_data="add_more"),
            InlineKeyboardButton("No", callback_data="add_done"),
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(
            "Would you like to add another destination for the same source?",
            reply_markup=reply_markup)
        return ADD_DEST
    except Exception as e:
        logger.error("Invalid destination chat id: %s", e)
        await message.reply_text(
            "The destination chat ID appears invalid. Please enter a valid chat ID:"
        )
        return ADD_DEST


# ---------------------------
# Callback to decide whether to add more destinations
# ---------------------------
async def add_more_choice(update: Update,
                          context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None:
        message = update.message or update.effective_message
        if message is None:
            logger.error(
                "No callback query or message available in add_more_choice.")
            return ConversationHandler.END
        await message.reply_text(
            "Unexpected input. Please use the inline keyboard.")
        return await send_main_menu(update, context)
    await query.answer()
    if query.data == "add_more":
        await query.edit_message_text(
            "Please send me another **destination chat ID** for the same source:"
        )
        return ADD_DEST
    else:
        await query.edit_message_text("Forwarding rule updated.")
        return await send_main_menu(update, context)


# ---------------------------
# Remove Rule: Handle Source Chat ID for Removal
# ---------------------------
async def remove_source(update: Update,
                        context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    if message is None or not message.text:
        logger.error("No text message provided in remove_source state.")
        if message is not None:
            await message.reply_text(
                "No text message received. Please restart with /start.")
        return ConversationHandler.END

    # Ensure context.user_data is not None.
    if context.user_data is None:
        context.user_data = {}

    text = message.text.strip()
    try:
        source_chat_id = int(text)
        user = update.effective_user or (update.message.from_user
                                         if update.message else None)
        if user is None:
            logger.error("User information not available in remove_source.")
            await message.reply_text(
                "User information not available. Please restart with /start.")
            return ConversationHandler.END
        user_id = user.id
        rules = load_user_rules(user_id)
        src_key = str(source_chat_id)
        if src_key not in rules:
            await message.reply_text(
                "You don't have any rule for that source chat ID. Please enter a valid source chat ID:"
            )
            return REMOVE_SOURCE
        context.user_data['remove_source'] = source_chat_id
        await message.reply_text(
            f"Your destinations for source {source_chat_id} are: {rules[src_key]}\nPlease send me the destination chat ID you want to remove:"
        )
        return REMOVE_DEST
    except Exception as e:
        logger.error("Error processing source chat id for removal: %s", e)
        await message.reply_text("Please enter a valid source chat ID:")
        return REMOVE_SOURCE


# ---------------------------
# Remove Rule: Handle Destination Chat ID for Removal
# ---------------------------
async def remove_dest(update: Update,
                      context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message or update.effective_message
    if message is None or not message.text:
        logger.error("No text message provided in remove_dest state.")
        if message is not None:
            await message.reply_text(
                "No text message received. Please restart with /start.")
        return ConversationHandler.END
    if context.user_data is None:
        logger.error("context.user_data is None in remove_dest.")
        await message.reply_text(
            "User context data not available. Please restart with /start.")
        return ConversationHandler.END
    text = message.text.strip()
    try:
        dest_chat_id = int(text)
        user = update.effective_user or (update.message.from_user
                                         if update.message else None)
        if user is None:
            logger.error("User information not available in remove_dest.")
            await message.reply_text(
                "User information not available. Please restart with /start.")
            return ConversationHandler.END
        user_id = user.id
        source_chat_id = context.user_data.get('remove_source')
        if source_chat_id is None:
            await message.reply_text(
                "Source chat ID missing. Please restart the removal process.")
            return await send_main_menu(update, context)
        rules = load_user_rules(user_id)
        src_key = str(source_chat_id)
        if src_key not in rules or dest_chat_id not in rules[src_key]:
            await message.reply_text(
                "That destination is not set for the given source. Please enter a valid destination chat ID to remove:"
            )
            return REMOVE_DEST
        rules[src_key].remove(dest_chat_id)
        if not rules[src_key]:
            del rules[src_key]
        save_user_rules(user_id, rules)
        await message.reply_text("Destination removed successfully.")
        return await send_main_menu(update, context)
    except Exception as e:
        logger.error("Error processing destination removal: %s", e)
        await message.reply_text(
            "Please enter a valid destination chat ID to remove:")
        return REMOVE_DEST


# ---------------------------
# Global Message Forwarder
# ---------------------------
async def dynamic_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    src_chat_id = update.message.chat.id
    all_rules = get_all_user_rules()
    for user_id, rules in all_rules.items():
        if str(src_chat_id) in rules:
            for dest_chat_id in rules[str(src_chat_id)]:
                try:
                    await update.message.forward(chat_id=dest_chat_id)
                    logger.info("Forwarded message from %s to %s for user %s",
                                src_chat_id, dest_chat_id, user_id)
                except Exception as e:
                    logger.error("Failed to forward message from %s to %s: %s",
                                 src_chat_id, dest_chat_id, e)


# ---------------------------
# Conversation Handler Setup
# ---------------------------
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        MAIN_MENU: [CallbackQueryHandler(menu_choice)],
        ADD_SOURCE:
        [MessageHandler(filters.TEXT & ~filters.COMMAND, add_source)],
        ADD_DEST: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, add_dest),
            CallbackQueryHandler(add_more_choice,
                                 pattern="^(add_more|add_done)$"),
        ],
        REMOVE_SOURCE:
        [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_source)],
        REMOVE_DEST:
        [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_dest)],
    },
    fallbacks=[CommandHandler("cancel", start)],
)


# ---------------------------
# Main Function
# ---------------------------
async def main():
    start_webserver()
    token = os.environ.get("TOKEN")
    if not token:
        logger.error("No TOKEN environment variable set!")
        return
    app_bot = ApplicationBuilder().token(token).build()
    app_bot.add_handler(conv_handler)
    app_bot.add_handler(
        MessageHandler(filters.ChatType.GROUPS, dynamic_forward))
    app_bot.run_polling()


if __name__ == '__main__':
    asyncio.run(main())
