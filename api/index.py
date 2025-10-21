# Файл: api/index.py (Версия с аварийным оповещением в Telegram)

import os
import httpx
import traceback # <-- НОВЫЙ ИМПОРТ для детальных ошибок
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
DATABASE_URL = os.getenv("POSTGRES_PRISMA_URL") # Используем переменную от Supabase
HUGGING_FACE_MODEL_URL = "https://api-inference.huggingface.co/models/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-fein-tuned"

# Константы
MOD_THRESHOLD_DEFAULT = 0.85
STATE_AWAITING_NEW_THRESHOLD = 'AWAITING_NEW_THRESHOLD'

# --- 2. DATABASE SETUP (SQLAlchemy) С АВАРИЙНЫМ ОПОВЕЩЕНИЕМ ---
Base = declarative_base()
SessionLocal = None

try:
    # Проверяем наличие переменной до попытки подключения
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена! Проверьте настройки Vercel.")

    # Адаптация строки подключения для asyncpg
    db_url_adapted = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    engine = create_engine(db_url_adapted)

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    print("INFO: База данных успешно настроена.") # Это сообщение мы так и не увидели

except Exception as e:
    # --- НОВЫЙ БЛОК: АВАРИЙНОЕ ОПОВЕЩЕНИЕ В TELEGRAM ---
    # Если что-то пошло не так, мы не просто падаем, а отправляем ошибку админу
    print(f"FATAL ERROR: {e}") # Оставляем лог на всякий случай
    
    # Формируем детальное сообщение об ошибке
    error_trace = traceback.format_exc()
    error_message = (
        "🔴 КРИТИЧЕСКАЯ ОШИБКА БОТА 🔴\n\n"
        "Не удалось подключиться к базе данных.\n\n"
        f"Переменная POSTGRES_PRISMA_URL: {'Найдена' if DATABASE_URL else 'НЕ НАЙДЕНА'}\n\n"
        f"Текст ошибки:\n{e}\n\n"
        f"Полная трассировка:\n{error_trace}"
    )

    # Используем httpx для прямой отправки сообщения через Telegram API
    # Это сработает, даже если остальная часть бота не инициализировалась
    if TELEGRAM_TOKEN and ADMIN_CHAT_ID:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": ADMIN_CHAT_ID,
            "text": error_message[:4096] # Обрезаем до макс. длины сообщения
        }
        try:
            with httpx.Client() as client:
                client.post(url, json=payload)
        except Exception as http_e:
            print(f"ERROR: Не удалось даже отправить аварийное сообщение: {http_e}")
    # --- КОНЕЦ АВАРИЙНОГО БЛОКА ---


# --- Все остальные секции кода остаются без изменений ---

# ... (API-клиент, инициализация бота, хендлеры и т.д. ... )
# Копипастить весь остальной код не нужно, он идентичен предыдущей версии.
# Главное, что изменен блок Try/Except выше.

# --- 4. BOT & APP INITIALIZATION ---
application: Optional[Application] = None
bot: Optional[Bot] = None
try:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot = application.bot
    print("INFO: Application (v20+) инициализирован.")
except Exception as e:
    print(f"ERROR: Не удалось создать Application: {e}")
    application = None
    bot = None

app = FastAPI()

# --- Хендлеры и все остальное ---
# (Здесь идет остальная часть вашего кода, которая не меняется)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if SessionLocal is None:
        # Теперь это сообщение увидят только обычные пользователи, админ получит детальную ошибку
        await update.message.reply_text("⚠️ Ошибка: База данных не инициализирована. Администратор уведомлен.")
        return
    db = SessionLocal()
    try:
        welcome_text = get_db_setting(db, 'welcome_text',
            "🎉 Привет! Бот запущен, БД подключена. Я готов к работе! Используйте /admin для настроек.")
    finally:
        db.close()
    await update.message.reply_text(welcome_text)

# Остальные функции (без изменений)
async def classify_text_huggingface(text: str, labels: list) -> Optional[dict]:
    if not HUGGING_FACE_TOKEN: return None
    headers = {"Authorization": f"Bearer {HUGGING_FACE_TOKEN}"}
    payload = {"inputs": text, "parameters": {"candidate_labels": labels, "multi_label": True}}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(HUGGING_FACE_MODEL_URL, headers=headers, json=payload, timeout=10.0)
            return response.json() if response.status_code == 200 else None
        except httpx.RequestError as e: return None
def get_db_setting(db_session, key: str, default: str) -> str:
    setting = db_session.query(Settings).filter_by(setting_key=key).first()
    return setting.setting_value if setting else default
#... и так далее для всех остальных функций ...

# --- 7. REGISTER HANDLERS ---
if application:
    application.add_handler(CommandHandler("start", start))
    # ... и все остальные хендлеры
else:
    print("ERROR: Application не инициализирован, хэндлеры не добавлены.")

# --- 8. WEB SERVER ENDPOINTS ---
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
        return {"status": "error", "message": str(e)}

@app.get("/")
def health_check():
    return {"status": "Bot is alive and ready for Vercel!"}
