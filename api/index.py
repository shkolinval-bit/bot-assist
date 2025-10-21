# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v15.1 - с логами AI)

import os
import asyncio
import httpx
from fastapi import FastAPI, Request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("POSTGRES_PRISMA_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
HUGGING_FACE_TOKEN = os.getenv("HUGGING_FACE_TOKEN")
HUGGING_FACE_MODEL_URL = "https://api-inference.huggingface.co/models/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-fein-tuned"

# Константы
MOD_THRESHOLD_DEFAULT = 0.85
STATE_AWAITING_NEW_THRESHOLD = 'AWAITING_NEW_THRESHOLD'

# --- 2. DATABASE SETUP (СИНХРОННОЕ) ---
Base = declarative_base()
engine = None
SessionLocal = None

try:
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена!")
    main_db_url = DATABASE_URL.split('?')[0]
    db_url_adapted = main_db_url.replace("postgres://", "postgresql+pg8000://", 1)
    engine = create_engine(db_url_adapted)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine, checkfirst=True)
    print("INFO: Синхронный Engine и SessionLocal успешно созданы.")
except Exception as e:
    print(f"FATAL ERROR during engine setup: {e}")

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

# --- ИНИЦИАЛИЗАЦИЯ FastAPI ---
app = FastAPI()

# --- СИНХРОННЫЕ ФУНКЦИИ-ПОМОЩНИКИ ДЛЯ БД ---
def get_db_setting(session, key: str, default: str) -> str:
    setting = session.query(Settings).filter_by(setting_key=key).first()
    return setting.setting_value if setting else default

def set_db_setting(session, key: str, value: str):
    setting = session.query(Settings).filter_by(setting_key=key).first()
    if setting:
        setting.setting_value = value
    else:
        session.add(Settings(setting_key=key, setting_value=value))
    session.commit()

def find_faq_response(session, message_text: str) -> str | None:
    message_words = set(message_text.lower().split())
    active_faqs = session.query(FAQ).filter(FAQ.enabled == True).all()
    for faq in active_faqs:
        faq_keywords = set(faq.keywords.lower().split(','))
        if any(word in message_words for word in faq_keywords):
            return faq.response_text
    return None

def add_faq_db(session, keywords: str, response_text: str):
    new_faq = FAQ(keywords=keywords, response_text=response_text, enabled=True)
    session.add(new_faq)
    session.commit()

# --- АСИНХРОННЫЕ ФУНКЦИИ-ПОМОЩНИКИ ДЛЯ AI ---
async def classify_text_huggingface(text: str, labels: list) -> Optional[dict]:
    if not HUGGING_FACE_TOKEN: 
        print("LOG_AI: HUGGING_FACE_TOKEN не найден.")
        return None
    headers = {"Authorization": f"Bearer {HUGGING_FACE_TOKEN}"}
    payload = {"inputs": text, "parameters": {"candidate_labels": labels, "multi_label": True}}
    async with httpx.AsyncClient() as client:
        try:
            print("LOG_AI: Отправка запроса в Hugging Face...")
            response = await client.post(HUGGING_FACE_MODEL_URL, headers=headers, json=payload, timeout=10.0)
            print(f"LOG_AI: Hugging Face ответил со статусом {response.status_code}.")
            return response.json() if response.status_code == 200 else None
        except httpx.RequestError as e:
            print(f"LOG_AI_ERROR: Ошибка запроса к Hugging Face: {e}")
            return None

async def analyze_for_scam(text_to_analyze: str) -> bool:
    if not GEMINI_API_KEY: 
        print("LOG_AI: GEMINI_API_KEY не найден.")
        return False
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        prompt = (f"Проанализируй на финансовый спам, мошенничество или фишинг. Ответь только ДА или НЕТ.\n\nСообщение: \"{text_to_analyze}\"")
        print("LOG_AI: Отправка запроса в Gemini (анализ на скам)...")
        response = await model.generate_content_async(prompt)
        print("LOG_AI: Gemini (скам) ответил.")
        return response.text.strip().upper() == 'ДА'
    except Exception as e:
        print(f"LOG_AI_ERROR: Ошибка вызова Gemini API (скам): {e}")
        return False

async def generate_response(user_prompt: str) -> str:
    if not GEMINI_API_KEY: 
        print("LOG_AI: GEMINI_API_KEY не найден.")
        return "Ключ Gemini API не настроен."
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        print(f"LOG_AI: Отправка запроса в Gemini (генерация ответа) с промптом: '{user_prompt}'")
        response = await model.generate_content_async(user_prompt)
        print("LOG_AI: Gemini (генерация) ответил.")
        return response.text
    except Exception as e:
        print(f"LOG_AI_ERROR: Ошибка вызова Gemini API (генерация): {e}")
        return "Произошла ошибка при обращении к ИИ."

# --- HANDLERS (ГИБРИДНЫЕ) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if SessionLocal is None:
        await update.message.reply_text("⚠️ Критическая ошибка конфигурации. SessionLocal не создан.")
        return
    session = SessionLocal()
    try:
        welcome_text = await asyncio.to_thread(get_db_setting, session, 'welcome_text', "🎉 Бот запущен!")
        await update.message.reply_text(welcome_text)
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при работе с БД: {e}")
    finally:
        await asyncio.to_thread(session.close)

async def add_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (код без изменений)
    pass

async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("LOG: Сработал обработчик упоминаний (handle_mention).") # --- ЛОГИРОВАНИЕ ---
    message_text = update.message.text
    mention_username = f"@{context.bot.username}"
    clean_text = message_text.replace(mention_username, "", 1).strip()
    if not clean_text:
        await update.message.reply_text("Я здесь. Спрашивайте! 🤖")
        return
    
    print("LOG: Запускаю проверку упоминания на скам.") # --- ЛОГИРОВАНИЕ ---
    if await analyze_for_scam(clean_text):
        print("LOG: Упоминание определено как скам. Удаляю.") # --- ЛОГИРОВАНИЕ ---
        await update.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Вопрос удален ИИ. Причина: Мошенничество/спам.")
        return
    
    await update.message.reply_text("Думаю... (использую Gemini)")
    response = await generate_response(clean_text)
    await update.message.reply_text(response)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"LOG: Сработал основной обработчик сообщений (handle_message) для: '{update.message.text}'") # --- ЛОГИРОВАНИЕ ---
    message_text = update.message.text
    
    if await analyze_for_scam(message_text):
        print("LOG: Сообщение определено как скам. Удаляю.") # --- ЛОГИРОВАНИЕ ---
        await update.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Сообщение удалено ИИ (Gemini). Причина: Обнаружено мошенничество/спам.")
        return

    # ... (остальная логика handle_message без изменений)

# ... (остальные хендлеры: admin_menu и т.д.)

# --- WEB SERVER ENDPOINTS ---
@app.post("/api/webhook")
async def webhook(request: Request):
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Регистрация всех хендлеров
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addfaq", add_faq))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity("mention"), handle_mention))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # ... (другие хендлеры)
    
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.initialize()
        await application.process_update(update)
        await application.shutdown()
        return {"status": "ok"}
    except Exception as e:
        print(f"ERROR: Ошибка в обработчике вебхука: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/")
def health_check():
    return {"status": "Бот жив. Версия v15.1 (с логами AI)."}
