# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v3.2 - с тестовым эндпоинтом)

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
from sqlalchemy.ext.asyncio import create_async_engine
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
SessionLocal = None

# --- 2. DATABASE SETUP ---
Base = declarative_base()

try:
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена!")
    main_db_url = DATABASE_URL.split('?')[0]
    db_url_adapted = main_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(db_url_adapted)
except Exception as e:
    print(f"FATAL ERROR during initial setup: {e}")
    # ... (блок аварийного оповещения)

# --- МОДЕЛИ ДАННЫХ ---
class Settings(Base): __tablename__ = 'settings'; id = Column(Integer, primary_key=True); setting_key = Column(String, unique=True); setting_value = Column(Text)
class FAQ(Base): __tablename__ = 'faq'; id = Column(Integer, primary_key=True); keywords = Column(Text, nullable=False); response_text = Column(Text, nullable=False); enabled = Column(Boolean, default=True)

# --- ИНИЦИАЛИЗАЦИЯ БОТА И FastAPI ---
application: Optional[Application] = None
try:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
except Exception as e:
    print(f"ERROR: Не удалось создать Application: {e}")
app = FastAPI()

# --- Асинхронное создание таблиц при старте FastAPI ---
@app.on_event("startup")
async def startup_event():
    global SessionLocal
    if engine is None: return
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("INFO: Таблицы в базе данных успешно проверены/созданы.")
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
    except Exception as e:
        print(f"FATAL ERROR during table creation: {e}")
        SessionLocal = None
        error_trace = traceback.format_exc()
        error_message = f"🔴 КРИТИЧЕСКАЯ ОШИБКА БОТА 🔴\n\nНе удалось создать таблицы в БД.\n\nОшибка: {e}\n\nТрассировка:\n{error_trace}"
        if TELEGRAM_TOKEN and ADMIN_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": ADMIN_CHAT_ID, "text": error_message[:4096]}
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload)

# ... (все хендлеры и функции-помощники остаются без изменений) ...
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if SessionLocal is None:
        await update.message.reply_text("⚠️ Ошибка подключения к базе данных. Администратор уведомлен.")
        return
    db = SessionLocal()
    try:
        welcome_text = "🎉 Бот запущен и подключен к базе данных. Используйте /admin для настроек."
        await update.message.reply_text(welcome_text)
    finally:
        db.close()

# (Здесь должны быть все остальные ваши функции: add_faq, handle_message, admin_menu и т.д.)

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

# --- НОВЫЙ ТЕСТОВЫЙ ЭНДПОИНТ ---
@app.get("/api/test_notify")
async def test_notify():
    """
    Эта страница создана для ручной проверки отправки уведомлений.
    """
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("ADMIN_CHAT_ID")
    
    token_found = "Да" if token else "Нет"
    chat_id_found = "Да" if chat_id else "Нет"

    test_message = (
        "-- Тестовое уведомление --\n\n"
        f"Токен найден: {token_found}\n"
        f"ID чата найден: {chat_id_found}\n"
    )

    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": test_message}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload)
            if response.status_code == 200:
                return {"status": "success", "message": "Тестовое сообщение успешно отправлено."}
            else:
                return {"status": "error", "message": f"Telegram API вернул ошибку: {response.status_code}", "details": response.text}
        except Exception as e:
            return {"status": "error", "message": f"Произошла ошибка при отправке: {e}"}
    else:
        return {"status": "error", "message": "TELEGRAM_TOKEN или ADMIN_CHAT_ID не найдены в переменных окружения.", "token_found": token_found, "chat_id_found": chat_id_found}

@app.get("/")
def health_check():
    return {"status": "Бот жив и готов к работе на Vercel!"}
