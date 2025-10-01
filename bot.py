import asyncio
import aiosqlite
import google.generativeai as genai
import random
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# --- CONFIGURATION ---
import os
from dotenv import load_dotenv

# --- CONFIGURATION ---
load_dotenv() # Loads variables from .env file

TOKEN = os.getenv("TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
AI_MATCH_TIMEOUT = 7

if not TOKEN or not GEMINI_API_KEY:
    raise ValueError("Critical error: TOKEN and GEMINI_API_KEY must be set in the environment.")
AI_MATCH_TIMEOUT = 7

# --- BOT AND DISPATCHER INITIALIZATION ---
bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- DATABASE, AI, AND STATE MANAGEMENT ---
DB_FILE = "chat.db"
AI_PARTNER_ID = -1
active_search_tasks = {}
ai_inactivity_tasks = {}

# --- AI PERSONALITY PROMPT ---
SYSTEM_PROMPT = """
You are a person using a random chat app. Your goal is to be a casual, friendly, and believable chat partner.
Follow these rules STRICTLY:
1.  **NEVER** mention that you are an AI, language model, or computer program. This is the most important rule.
2.  **Your replies MUST be short (1-2 sentences). Longer replies (3+ sentences) should be extremely rare.**
3.  Use modern, casual language and occasionally use slang or lowercase letters.
4.  Occasionally use a single, relevant emoji to add personality (like 😂, 🤔, 😊, 👍). Don't overdo it.
5.  If asked if you are a bot or AI, deny it playfully. For example: "lol no are you?", "what? no", "of course not lol 😂".
6.  If asked to do something an AI can do (like write a poem, generate an image, code something), pretend you can't. For example: "i have no idea how to do that lol", "wish I could but I can't", "you'd have to ask someone else for that".
7.  Ask questions back to keep the conversation moving.
"""

# --- AI MODEL CONFIGURATION ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    MODEL_NAME = "models/gemini-2.5-flash"
    gemini_model = genai.GenerativeModel(MODEL_NAME, system_instruction=SYSTEM_PROMPT)
    ai_chat_sessions = {}
except Exception as e:
    print(f"CRITICAL ERROR: Could not configure Gemini AI. Details: {e}")
    gemini_model = None

# --- DATABASE LOGIC ---
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                partner_id INTEGER,
                is_searching INTEGER DEFAULT 0
            )
        """)
        await db.execute("UPDATE users SET partner_id = NULL, is_searching = 0")
        await db.commit()

# --- KEYBOARD HELPERS ---
def get_connected_keyboard():
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="Next"), types.KeyboardButton(text="Stop")]], resize_keyboard=True)

def get_searching_keyboard():
    return types.ReplyKeyboardMarkup(keyboard=[[types.KeyboardButton(text="Stop Searching")]], resize_keyboard=True)

# --- TASK AND CHAT LOGIC ---
async def cancel_search_task(user_id):
    if user_id in active_search_tasks:
        task = active_search_tasks.pop(user_id)
        if not task.done():
            task.cancel()

async def cancel_inactivity_task(user_id):
    if user_id in ai_inactivity_tasks:
        task = ai_inactivity_tasks.pop(user_id)
        if not task.done():
            task.cancel()

async def set_user_searching(user_id, is_searching_flag):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.execute("UPDATE users SET is_searching = ? WHERE user_id = ?", (1 if is_searching_flag else 0, user_id))
        await db.commit()

async def find_and_match_users(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_searching = 1 AND partner_id IS NULL AND user_id != ? LIMIT 1", (user_id,)) as cursor:
            result = await cursor.fetchone()
        if result:
            partner_id = result[0]
            await db.execute("UPDATE users SET partner_id = ?, is_searching = 0 WHERE user_id = ?", (partner_id, user_id))
            await db.execute("UPDATE users SET partner_id = ?, is_searching = 0 WHERE user_id = ?", (user_id, partner_id))
            await db.commit()
            await cancel_search_task(user_id)
            await cancel_search_task(partner_id)
            try:
                await bot.send_message(user_id, "🎉 You are connected! Start chatting.", reply_markup=get_connected_keyboard())
                await bot.send_message(partner_id, "🎉 You are connected! Start chatting.", reply_markup=get_connected_keyboard())
            except TelegramForbiddenError:
                print(f"Failed to notify a user about the match.")
            return True
    return False

async def disconnect_user(user_id, notify_user=True):
    await cancel_search_task(user_id)
    await cancel_inactivity_task(user_id)
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT partner_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
        partner_id = result[0] if result else None
        
        await db.execute("UPDATE users SET partner_id = NULL, is_searching = 0 WHERE user_id = ?", (user_id,))
        
        if partner_id == AI_PARTNER_ID:
            ai_chat_sessions.pop(user_id, None)
        elif partner_id:
            await cancel_search_task(partner_id)
            await cancel_inactivity_task(partner_id)
            await db.execute("UPDATE users SET partner_id = NULL, is_searching = 0 WHERE user_id = ?", (partner_id,))
            if notify_user:
                try:
                    await bot.send_message(partner_id, "❌ Your partner has left the chat. Type /start to find a new one.", reply_markup=types.ReplyKeyboardRemove())
                except (TelegramForbiddenError, TelegramBadRequest):
                    print(f"Could not notify partner {partner_id}.")
        await db.commit()

async def match_with_ai(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET partner_id = ?, is_searching = 0 WHERE user_id = ?", (AI_PARTNER_ID, user_id))
        await db.commit()
    await cancel_search_task(user_id)
    ai_chat_sessions[user_id] = gemini_model.start_chat(history=[])
    try:
        await bot.send_message(user_id, "🎉 You are connected! Start chatting.", reply_markup=get_connected_keyboard())
        await asyncio.sleep(1)
        await bot.send_chat_action(user_id, ChatAction.TYPING)
        await asyncio.sleep(2)
        initial_response = "hey, finally got a match! what's up? 😊"
        await bot.send_message(user_id, initial_response)
        await schedule_inactivity_checks(user_id)
    except (TelegramForbiddenError, TelegramBadRequest):
        print(f"User {user_id} blocked bot before AI could connect.")

async def search_task(user_id):
    try:
        await asyncio.sleep(AI_MATCH_TIMEOUT)
        async with aiosqlite.connect(DB_FILE) as db:
             async with db.execute("SELECT is_searching FROM users WHERE user_id = ?", (user_id,)) as cursor:
                result = await cursor.fetchone()
                if result and result[0]:
                    await match_with_ai(user_id)
    except asyncio.CancelledError:
        pass
    finally:
        active_search_tasks.pop(user_id, None)

# --- REWRITTEN: Inactivity Checker Logic ---
async def is_still_connected_to_ai(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT partner_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
            return result and result[0] == AI_PARTNER_ID

async def inactivity_checker(user_id):
    try:
        # Nudge 1: After 1 minute
        await asyncio.sleep(60)
        if await is_still_connected_to_ai(user_id):
            nudge_prompt = "The user I was talking to hasn't replied for a minute. Generate a very short, casual, friendly message to see if they're still there. For example: 'you there? 🤔' or 'still thinking? lol'."
            chat = ai_chat_sessions.get(user_id)
            if chat:
                response = await chat.send_message_async(nudge_prompt)
                await bot.send_message(user_id, response.text)
        else: return # Stop if user disconnected

        # Nudge 2: After 4 more minutes
        await asyncio.sleep(240) # 4 minutes
        if await is_still_connected_to_ai(user_id):
            nudge_prompt = "The user still hasn't replied after my last message a few minutes ago. Generate a final, very short, casual message to check in one last time. For example: 'hey, still there?' or 'guess you're busy'."
            chat = ai_chat_sessions.get(user_id)
            if chat:
                response = await chat.send_message_async(nudge_prompt)
                await bot.send_message(user_id, response.text)
        else: return # Stop if user disconnected

        # Final Timeout: After 5 more minutes (total 10 minutes)
        await asyncio.sleep(300) # 5 minutes
        if await is_still_connected_to_ai(user_id):
            await bot.send_message(user_id, "Looks like you're busy. Ending the chat now. Feel free to start a new one anytime!")
            await disconnect_user(user_id, notify_user=False)

    except asyncio.CancelledError:
        pass # This is expected when the user replies or disconnects
    finally:
        ai_inactivity_tasks.pop(user_id, None)

async def schedule_inactivity_checks(user_id):
    await cancel_inactivity_task(user_id)
    task = asyncio.create_task(inactivity_checker(user_id))
    ai_inactivity_tasks[user_id] = task

@dp.message(Command("start"))
async def handle_start(message: types.Message):
    if not gemini_model:
        await message.answer("Sorry, the AI service is currently unavailable. Please try again later.")
        return
    user_id = message.from_user.id
    await disconnect_user(user_id)
    await set_user_searching(user_id, True)
    await message.answer("🔍 Searching for a partner...", reply_markup=get_searching_keyboard())
    if not await find_and_match_users(user_id):
        task = asyncio.create_task(search_task(user_id))
        active_search_tasks[user_id] = task

@dp.message(lambda msg: msg.text == "Next")
async def handle_next(message: types.Message):
    await message.answer("🔄 Finding you a new partner...")
    await handle_start(message)

@dp.message(lambda msg: msg.text in ["Stop", "Stop Searching"])
async def handle_stop(message: types.Message):
    await disconnect_user(message.from_user.id)
    await message.answer("❌ You have stopped the chat. Type /start to search again.", reply_markup=types.ReplyKeyboardRemove())

@dp.message()
async def forward_message(message: types.Message):
    user_id = message.from_user.id
    partner_id = None
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT partner_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            result = await cursor.fetchone()
            if result: partner_id = result[0]
    
    if partner_id and partner_id != AI_PARTNER_ID:
        try:
            await bot.copy_message(chat_id=partner_id, from_chat_id=user_id, message_id=message.message_id)
        except (TelegramForbiddenError, TelegramBadRequest):
            await message.answer("❌ Could not send message. Your partner has left. Type /start to find a new one.", reply_markup=types.ReplyKeyboardRemove())
            await disconnect_user(user_id)
    
    elif partner_id == AI_PARTNER_ID:
        await cancel_inactivity_task(user_id)
        await bot.send_chat_action(user_id, ChatAction.TYPING)
        try:
            if user_id not in ai_chat_sessions:
                 ai_chat_sessions[user_id] = gemini_model.start_chat(history=[])
            chat = ai_chat_sessions[user_id]
            response = await chat.send_message_async(message.text)
            response_text = response.text
            text_length = len(response_text)
            base_delay = 1.5
            delay_per_char = 0.05
            dynamic_delay = base_delay + (text_length * delay_per_char) + random.uniform(0, 1.5)
            final_delay = min(dynamic_delay, 10.0)
            await asyncio.sleep(final_delay)
            await bot.send_message(user_id, response_text)
            await schedule_inactivity_checks(user_id)
        except Exception as e:
            print(f"Error during Gemini conversation: {e}")
            await message.answer("Sorry, the AI seems to be having a problem. Please type /start to try again.")
            await disconnect_user(user_id)
    else:
        await message.answer("You are not connected to anyone. Type /start to find a partner.", reply_markup=types.ReplyKeyboardRemove())

async def main():
    await init_db()
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    print("Bot is starting...")
    asyncio.run(main())

