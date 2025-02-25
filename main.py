import os
import json
import random
import logging
import asyncio
from threading import Thread

from openai import OpenAI
from flask import Flask
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
)

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TOKEN")
# Initialize OpenAI client
client = OpenAI(api_key=os.environ.get("OPENAI_KEY"))

# File for storing words (WORD, PINYIN, CATEGORY)
WORD_LIB_FILE = "word_library.json"

# Conversation states
MAIN_MENU, ADD_WORD, CHOOSE_CATEGORY, WAITING_ANSWER = range(4)


# --- Utility Functions for JSON Handling ---
def load_json(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_word_library():
    return load_json(WORD_LIB_FILE)


def save_word_library(library):
    save_json(WORD_LIB_FILE, library)


# --- ChatGPT Functions ---
async def generate_question(word: str, pinyin: str, category: str) -> str:
    prompt = (
        f"You are a Chinese language teacher who specializes in professional contexts. "
        f"I have the following vocabulary entry:\n"
        f"Word: {word}\n"
        f"Pinyin: {pinyin}\n"
        f"Category/Context: {category}\n\n"
        f"Generate a simple plain text test question for a Chinese learner. "
        f"The question should ask the student to provide either the pinyin for the word or the Chinese character, "
        f"tailored to the given context. Do not include any extra formatting or JSON; simply provide a clear question."
    )
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful Chinese language teacher."
                },
                {
                    "role": "user",
                    "content": prompt
                },
            ],
            temperature=0.7,
            max_tokens=100,
        )
        if (response.choices and response.choices[0].message
                and response.choices[0].message.content is not None):
            question = response.choices[0].message.content.strip()
            return question
        else:
            logger.error(
                "No valid content returned from Chat API for generate_question."
            )
            return f"What is the pinyin for the Chinese character '{word}'?"
    except Exception as e:
        logger.error("Error generating question: %s", e)
        return f"What is the pinyin for the Chinese character '{word}'?"


async def evaluate_answer(word: str, pinyin: str, category: str,
                          user_answer: str) -> str:
    prompt = (
        f"You are a strict Chinese language teacher who assesses professional vocabulary usage. "
        f"Given the following word details:\n"
        f"Word: {word}\n"
        f"Pinyin: {pinyin}\n"
        f"Context: {category}\n\n"
        f"The student answered: \"{user_answer}\".\n"
        f"Determine if the student's answer is correct. Respond with only 'Correct' if it is correct, or 'Incorrect' if not."
    )
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict Chinese language teacher."
                },
                {
                    "role": "user",
                    "content": prompt
                },
            ],
            temperature=0,
            max_tokens=20,
        )
        if (response.choices and response.choices[0].message
                and response.choices[0].message.content is not None):
            evaluation = response.choices[0].message.content.strip()
            return "Correct" if "correct" in evaluation.lower(
            ) else "Incorrect"
        else:
            logger.error(
                "No valid content returned from Chat API for evaluate_answer.")
            return "Correct" if user_answer.lower() == pinyin.lower(
            ) else "Incorrect"
    except Exception as e:
        logger.error("Error evaluating answer: %s", e)
        return "Correct" if user_answer.lower() == pinyin.lower(
        ) else "Incorrect"


# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update is None or update.message is None or update.message.text is None:
        return MAIN_MENU
    keyboard = [
        [
            InlineKeyboardButton("Edit Word Library",
                                 callback_data="edit_library")
        ],
        [InlineKeyboardButton("Study/Test", callback_data="study")],
    ]
    await update.message.reply_text(
        "Welcome to the Chinese Learning Bot! Please choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return MAIN_MENU


async def main_menu_handler(update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query if update and update.callback_query else None
    data = query.data if query and query.data is not None else (
        update.message.text if update and update.message else None)

    if data is None:
        return MAIN_MENU

    if context.user_data is None:
        return MAIN_MENU

    if query:
        await query.answer()

    if data == "edit_library":
        keyboard = [
            [InlineKeyboardButton("Add Word", callback_data="add_word")],
            [
                InlineKeyboardButton("View Library",
                                     callback_data="view_library")
            ],
            [InlineKeyboardButton("Back", callback_data="back_main")],
        ]
        if query:
            await query.edit_message_text(
                "Word Library Options:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif update.message:
            await update.message.reply_text(
                "Word Library Options:",
                reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "study":
        library = load_word_library()
        if not library:
            if query:
                await query.edit_message_text(
                    "Your word library is empty! Please add words first.")
            elif update.message:
                await update.message.reply_text(
                    "Your word library is empty! Please add words first.")
            return MAIN_MENU
        categories = set(
            entry.get("category", "Uncategorized") for entry in library)
        keyboard = [[
            InlineKeyboardButton("All Categories", callback_data="cat_all")
        ]]
        for cat in sorted(categories):
            keyboard.append(
                [InlineKeyboardButton(cat, callback_data=f"cat_{cat}")])
        if query:
            await query.edit_message_text(
                "Select a category for testing:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        elif update.message:
            await update.message.reply_text(
                "Select a category for testing:",
                reply_markup=InlineKeyboardMarkup(keyboard))
        return CHOOSE_CATEGORY
    elif data == "back_main":
        if query:
            await query.edit_message_text("Returning to main menu...")
        elif update.message:
            await update.message.reply_text("Returning to main menu...")
        await start(update, context)
    elif data == "add_word":
        if query:
            await query.edit_message_text(
                "Please send a new word in the following format:\n\nWORD,PINYIN,CATEGORY\n\nExample: 停, Tíng, Working in a web3 company"
            )
        elif update.message:
            await update.message.reply_text(
                "Please send a new word in the following format:\n\nWORD,PINYIN,CATEGORY\n\nExample: 停, Tíng, Working in a web3 company"
            )
        return ADD_WORD
    elif data == "view_library":
        library = load_word_library()
        if not library:
            if query:
                await query.edit_message_text("Your word library is empty!")
            elif update.message:
                await update.message.reply_text("Your word library is empty!")
        else:
            text = "Current Word Library:\n"
            for idx, entry in enumerate(library):
                text += f"{idx}: {entry['word']} - {entry['pinyin']} (Category: {entry.get('category','Uncategorized')})\n"
            if query:
                await query.edit_message_text(text)
            elif update.message:
                await update.message.reply_text(text)
    elif data.startswith("cat_"):
        selected_category = data.split("cat_")[1]
        context.user_data["selected_category"] = selected_category
        if query:
            await query.edit_message_text(
                f"Selected category: {selected_category}. Starting study session..."
            )
        elif update.message:
            await update.message.reply_text(
                f"Selected category: {selected_category}. Starting study session..."
            )
        await start_study(update, context)
    return MAIN_MENU


async def add_word(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update is None or update.message is None or update.message.text is None:
        return MAIN_MENU

    text = update.message.text.strip()
    parts = text.split(',')
    if len(parts) != 3:
        await update.message.reply_text(
            "Incorrect format. Please use: WORD,PINYIN,CATEGORY")
        return ADD_WORD

    entry = {
        "word": parts[0].strip(),
        "pinyin": parts[1].strip(),
        "category": parts[2].strip()
    }
    library = load_word_library()
    library.append(entry)
    save_word_library(library)
    await update.message.reply_text(
        f"Word '{entry['word']}' added successfully!")
    return MAIN_MENU


async def start_study(update: Update,
                      context: ContextTypes.DEFAULT_TYPE) -> int:
    if context is None or not hasattr(
            context, "user_data") or context.user_data is None:
        return MAIN_MENU

    library = load_word_library()
    selected_category = context.user_data.get("selected_category", "all")
    if selected_category.lower() != "all":
        filtered = [
            entry for entry in library
            if entry.get("category", "").lower() == selected_category.lower()
        ]
        if not filtered:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    f"No words found for category '{selected_category}'.")
            elif update.message:
                await update.message.reply_text(
                    f"No words found for category '{selected_category}'.")
            return MAIN_MENU
        word_entry = random.choice(filtered)
    else:
        if not library:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    "Your word library is empty!")
            elif update.message:
                await update.message.reply_text("Your word library is empty!")
            return MAIN_MENU
        word_entry = random.choice(library)

    word = word_entry["word"]
    pinyin = word_entry["pinyin"]
    category = word_entry.get("category", "General")

    context.user_data["current_word"] = word
    context.user_data["current_pinyin"] = pinyin
    context.user_data["current_category"] = category

    question = await generate_question(word, pinyin, category)
    if update.callback_query:
        await update.callback_query.edit_message_text(question)
    elif update.message:
        await update.message.reply_text(question)
    return WAITING_ANSWER


async def check_user_answer(update: Update,
                            context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("check_user_answer triggered, update: %s", update)
    if update is None or update.message is None or update.message.text is None:
        logger.warning("Update or update.message.text is None")
        return MAIN_MENU
    if context is None or not hasattr(
            context, "user_data") or context.user_data is None:
        logger.warning("Context or context.user_data is None")
        return MAIN_MENU
    user_answer = update.message.text.strip()
    word = context.user_data.get("current_word", "")
    pinyin = context.user_data.get("current_pinyin", "")
    category = context.user_data.get("current_category", "General")

    logger.info("User answered: '%s' for word: '%s'", user_answer, word)
    evaluation = await evaluate_answer(word, pinyin, category, user_answer)
    await update.message.reply_text(f"Your answer is: {evaluation}")

    keyboard = [
        [InlineKeyboardButton("Continue Testing", callback_data="study")],
        [InlineKeyboardButton("Back to Main Menu", callback_data="back_main")],
    ]
    await update.message.reply_text(
        "What would you like to do next?",
        reply_markup=InlineKeyboardMarkup(keyboard))
    return MAIN_MENU


# --- Keep Alive Web Server ---
app = Flask('')


@app.route('/')
def home():
    return "I am alive!"


def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))


def keep_alive():
    t = Thread(target=run_flask)
    t.start()


# --- Main Function ---
def main():
    TOKEN = os.environ.get("TOKEN")
    if not TOKEN:
        print("Error: No TOKEN provided in environment variables.")
        return

    # Start the keep-alive server
    keep_alive()

    application = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(main_menu_handler)],
            ADD_WORD:
            [MessageHandler(filters.TEXT & ~filters.COMMAND, add_word)],
            CHOOSE_CATEGORY: [CallbackQueryHandler(main_menu_handler)],
            WAITING_ANSWER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND,
                               check_user_answer)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    application.add_handler(conv_handler)
    application.run_polling()


if __name__ == '__main__':
    main()
