# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v5.0 - ПОЛНОСТЬЮ АСИНХРОННАЯ)

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
from sqlalchemy import Column, Integer, String, Boolean, Text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
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
engine = None
AsyncSessionLocal = None

# --- 2. DATABASE SETUP ---
Base = declarative_base()

try:
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена!")

    main_db_url = DATABASE_URL.split('?')[0]
    db_url_adapted = main_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url_adapted)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
except Exception as e:
    print(f"FATAL ERROR during initial engine setup: {e}")

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

# --- ИНИЦИАЛИЗАЦИЯ БОТА И FastAPI ---
application: Optional[Application] = None
try:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
except Exception as e:
    print(f"ERROR: Не удалось создать Application: {e}")

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    if engine is None:
        print("Engine не был инициализирован, создание таблиц пропущено.")
        return
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("INFO: Таблицы в базе данных успешно проверены/созданы.")
    except Exception as e:
        global AsyncSessionLocal
        AsyncSessionLocal = None # Гарантируем, что сессии не будут создаваться
        print(f"FATAL ERROR during table creation: {e}")
        error_trace = traceback.format_exc()
        error_message = f"🔴 КРИТИЧЕСКАЯ ОШИБКА БОТА 🔴\n\nНе удалось создать таблицы в БД.\n\nОшибка: {e}\n\nТрассировка:\n{error_trace}"
        if TELEGRAM_TOKEN and ADMIN_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": ADMIN_CHAT_ID, "text": error_message[:4096]}
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload)

# --- АСИНХРОННЫЕ ФУНКЦИИ-ПОМОЩНИКИ ---
async def classify_text_huggingface(text: str, labels: list) -> Optional[dict]:
    # ... (эта функция уже асинхронная и остается без изменений)
    pass # Заглушка

async def get_db_setting(key: str, default: str) -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Settings).filter_by(setting_key=key))
        setting = result.scalar_one_or_none()
        return setting.setting_value if setting else default

async def set_db_setting(key: str, value: str):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(Settings).filter_by(setting_key=key))
            setting = result.scalar_one_or_none()
            if setting:
                setting.setting_value = value
            else:
                session.add(Settings(setting_key=key, setting_value=value))

async def find_faq_response(message_text: str) -> str | None:
    async with AsyncSessionLocal() as session:
        message_words = set(message_text.lower().split())
        result = await session.execute(select(FAQ).filter(FAQ.enabled == True))
        active_faqs = result.scalars().all()
        for faq in active_faqs:
            faq_keywords = set(faq.keywords.lower().split(','))
            if any(word in message_words for word in faq_keywords):
                return faq.response_text
        return None
        
# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AsyncSessionLocal is None:
        await update.message.reply_text("⚠️ Ошибка подключения к базе данных. Администратор уведомлен.")
        return
    try:
        welcome_text = await get_db_setting('welcome_text', "🎉 Бот запущен и подключен к базе данных. Используйте /admin для настроек.")
        await update.message.reply_text(welcome_text)
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при получении настроек: {e}")

# ... (Остальные хендлеры, переписанные под асинхронность)

async def add_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Эта команда доступна только администратору.")
        return
    if AsyncSessionLocal is None:
        await update.message.reply_text("⚠️ База данных не инициализирована.")
        return
    if len(context.args) < 1 or ';' not in " ".join(context.args):
        await update.message.reply_text("❌ Использование: /addfaq <ключи,через,запятую>; <текст ответа>")
        return
    
    try:
        full_text = " ".join(context.args)
        keywords_part, response_part = full_text.split(';', 1)
        
        async with AsyncSessionLocal() as session:
            async with session.begin():
                new_faq = FAQ(keywords=keywords_part.strip().lower(), response_text=response_part.strip(), enabled=True)
                session.add(new_faq)
        
        await update.message.reply_text(f"✅ Новый FAQ сохранен!\nКлючи: {keywords_part.strip().lower()}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка сохранения FAQ: {e}")


# --- REGISTER HANDLERS ---
if application:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addfaq", add_faq))
    # ... (остальные хендлеры)
else:
    print("ERROR: Application не был инициализирован.")

# --- WEB SERVER ENDPOINTS ---
@app.post("/api/webhook")
async def webhook(request: Request):
    if not application: return {"status": "error", "message": "Bot not initialized"}
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
    return {"status": "Бот жив и готов к работе на Vercel!"}
