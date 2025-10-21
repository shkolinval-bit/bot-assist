# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ)

import os
import httpx
import traceback
from fastapi import FastAPI, Request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
HUGGING_FACE_TOKEN = os.getenv("HUGGING_FACE_TOKEN")
DATABASE_URL = os.getenv("POSTGRES_PRISMA_URL")
HUGGING_FACE_MODEL_URL = "https://api-inference.huggingface.co/models/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-fein-tuned"

# Константы
MOD_THRESHOLD_DEFAULT = 0.85
STATE_AWAITING_NEW_THRESHOLD = 'AWAITING_NEW_THRESHOLD'

# --- 2. DATABASE SETUP ---
Base = declarative_base()
SessionLocal = None

try:
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена!")

    # --- ФИНАЛЬНОЕ ИЗМЕНЕНИЕ: Убираем лишние параметры из URL, которые не понимает asyncpg ---
    main_db_url = DATABASE_URL.split('?')[0]
    
    # Адаптация строки подключения для asyncpg
    db_url_adapted = main_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    engine = create_engine(db_url_adapted)

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    print("INFO: База данных успешно настроена.")

except Exception as e:
    print(f"FATAL ERROR: {e}")
    error_trace = traceback.format_exc()
    error_message = (
        "🔴 КРИТИЧЕСКАЯ ОШИБКА БОТА 🔴\n\n"
        "Не удалось подключиться к базе данных.\n\n"
        f"Переменная POSTGRES_PRISMA_URL: {'Найдена' if DATABASE_URL else 'НЕ НАЙДЕНА'}\n\n"
        f"Текст ошибки:\n{e}\n\n"
        f"Полная трассировка:\n{error_trace}"
    )
    if TELEGRAM_TOKEN and ADMIN_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": ADMIN_CHAT_ID, "text": error_message[:4096]}
        try:
            with httpx.Client() as client:
                client.post(url, json=payload)
        except Exception as http_e:
            print(f"ERROR: Не удалось отправить аварийное сообщение: {http_e}")

# --- МОДЕЛИ ДАННЫХ ---
class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    setting_key = Column(String, unique=True)
    setting_value = Column(Text)

class FAQ(Base):
    __tablename__ = 'faq'
    id = Column(Integer, primary_key=True)
    keywords = Column(Text, nullable=False)
    response_text = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
application: Optional[Application] = None
bot: Optional[Bot] = None
try:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot = application.bot
except Exception as e:
    print(f"ERROR: Не удалось создать Application: {e}")

app = FastAPI()

# --- ФУНКЦИИ-ПОМОЩНИКИ ---
async def classify_text_huggingface(text: str, labels: list) -> Optional[dict]:
    if not HUGGING_FACE_TOKEN: return None
    headers = {"Authorization": f"Bearer {HUGGING_FACE_TOKEN}"}
    payload = {"inputs": text, "parameters": {"candidate_labels": labels, "multi_label": True}}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(HUGGING_FACE_MODEL_URL, headers=headers, json=payload, timeout=10.0)
            return response.json() if response.status_code == 200 else None
        except httpx.RequestError: return None

def get_db_setting(db_session, key: str, default: str) -> str:
    setting = db_session.query(Settings).filter_by(setting_key=key).first()
    return setting.setting_value if setting else default

def set_db_setting(db_session, key: str, value: str):
    setting = db_session.query(Settings).filter_by(setting_key=key).first()
    if setting: setting.setting_value = value
    else: db_session.add(Settings(setting_key=key, setting_value=value))
    db_session.commit()

async def analyze_for_scam(text_to_analyze: str) -> bool:
    if not GEMINI_API_KEY: return False
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        prompt = (f"Analyze for financial spam, fraud, or phishing. Answer only YES or NO.\n\nMessage: \"{text_to_analyze}\"")
        response = await model.generate_content_async(prompt)
        return response.text.strip().upper() == 'YES'
    except Exception as e:
        print(f"ERROR: Gemini API call failed: {e}")
        return False

async def generate_response(user_prompt: str) -> str:
    if not GEMINI_API_KEY: return "Gemini API key is not configured."
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        response = await model.generate_content_async(user_prompt)
        return response.text
    except Exception as e:
        print(f"ERROR: Gemini API call failed: {e}")
        return "Error contacting AI."

async def find_faq_response(message_text: str) -> str | None:
    if SessionLocal is None: return None
    db = SessionLocal()
    try:
        message_words = set(message_text.lower().split())
        active_faqs = db.query(FAQ).filter(FAQ.enabled == True).all()
        for faq in active_faqs:
            faq_keywords = set(faq.keywords.lower().split(','))
            if any(word in message_words for word in faq_keywords):
                return faq.response_text
    except OperationalError: print("ERROR: DB error during FAQ search.")
    finally: db.close()
    return None

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if SessionLocal is None:
        await update.message.reply_text("⚠️ Database connection error. Administrator has been notified.")
        return
    db = SessionLocal()
    try:
        welcome_text = get_db_setting(db, 'welcome_text', "🎉 Bot is running and connected to the database. Use /admin for settings.")
    finally: db.close()
    await update.message.reply_text(welcome_text)
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"Bot started by user {update.effective_user.name}.")
    except Exception as e:
        print(f"ERROR: Admin notification failed: {e}")

async def add_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ This command is for admins only.")
        return
    if SessionLocal is None:
        await update.message.reply_text("⚠️ Database not initialized.")
        return
    if len(context.args) < 1 or ';' not in " ".join(context.args):
        await update.message.reply_text("❌ Usage: /addfaq <keywords,comma,separated>; <response text>")
        return
    db = SessionLocal()
    try:
        full_text = " ".join(context.args)
        keywords_part, response_part = full_text.split(';', 1)
        new_faq = FAQ(keywords=keywords_part.strip().lower(), response_text=response_part.strip(), enabled=True)
        db.add(new_faq)
        db.commit()
        await update.message.reply_text(f"✅ New FAQ saved!\nKeywords: {keywords_part.strip().lower()}")
    except Exception as e:
        db.rollback()
        await update.message.reply_text(f"❌ Error saving FAQ: {e}")
    finally: db.close()

async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    mention_username = f"@{context.bot.username}"
    clean_text = message_text.replace(mention_username, "", 1).strip()
    if not clean_text:
        await update.message.reply_text("I'm here. Ask me anything! 🤖")
        return
    if await analyze_for_scam(clean_text):
        await update.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Question deleted by AI. Reason: Scam/spam.")
        return
    await update.message.reply_text("Thinking... (using Gemini)")
    response = await generate_response(clean_text)
    await update.message.reply_text(response)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    if await analyze_for_scam(message_text):
        await update.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Message deleted by AI (Gemini). Reason: Scam/spam detected.")
        return

    mod_threshold = MOD_THRESHOLD_DEFAULT
    if SessionLocal:
        db = SessionLocal()
        try: mod_threshold = float(get_db_setting(db, 'mod_threshold', str(MOD_THRESHOLD_DEFAULT)))
        finally: db.close()

    candidate_labels = ["toxicity", "job offer", "advertisement", "financial spam"]
    results = await classify_text_huggingface(message_text, candidate_labels)
    if results and results.get('labels') and results.get('scores'):
        best_label, best_score = results['labels'][0], results['scores'][0]
        if (best_label in ["toxicity", "advertisement"]) and best_score > mod_threshold:
            await update.message.delete()
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Message deleted by AI. Reason: {best_label} (Confidence: {best_score:.2%}).")
            return

    faq_answer = await find_faq_response(message_text)
    if faq_answer:
        await update.message.reply_text(faq_answer)
        return

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Access denied.")
        return
    context.user_data['state'] = None
    keyboard = [[InlineKeyboardButton("⚙️ Set Moderation Threshold", callback_data='admin_moderation')],]
    await update.message.reply_text('Admin Menu:', reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'admin_moderation':
        if SessionLocal is None:
            await query.message.edit_text("⚠️ Database not initialized.")
            return
        db = SessionLocal()
        try: current_threshold = get_db_setting(db, 'mod_threshold', str(MOD_THRESHOLD_DEFAULT))
        finally: db.close()
        text = f"Current Zero-Shot threshold: **{current_threshold}**\n\nEnter a new value (0.00 to 1.00):"
        context.user_data['state'] = STATE_AWAITING_NEW_THRESHOLD
        await query.message.edit_text(text, parse_mode='Markdown')

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == STATE_AWAITING_NEW_THRESHOLD:
        try:
            float_value = float(update.message.text)
            if not (0.0 <= float_value <= 1.0): raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Error: Please enter a number between 0.00 and 1.00.")
            return
        db = SessionLocal()
        try:
            set_db_setting(db, 'mod_threshold', str(float_value))
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Moderation threshold updated to: **{float_value}**.", parse_mode='Markdown')
        except Exception as e:
            db.rollback()
            await update.message.reply_text(f"❌ DB Error while saving: {e}")
        finally: db.close()

# --- REGISTER HANDLERS ---
if application:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addfaq", add_faq))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern='^admin_'))
    application.add_handler(MessageHandler(filters.TEXT & filters.User(user_id=int(ADMIN_CHAT_ID)) & ~filters.COMMAND, handle_admin_input))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity("mention"), handle_mention))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
else:
    print("ERROR: Application not initialized.")

# --- WEB SERVER ENDPOINTS ---
@app.post("/api/webhook")
async def webhook(request: Request):
    if not application or not bot:
        return {"status": "error", "message": "Bot not initialized"}
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.initialize()
        await application.process_update(update)
        await application.shutdown()
        return {"status": "ok"}
    except Exception as e:
        print(f"ERROR in webhook handler: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/")
def health_check():
    return {"status": "Bot is alive and ready for Vercel!"}
