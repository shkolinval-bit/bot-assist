# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v11.0 - ПОЛНАЯ ИНКАПСУЛЯЦИЯ)

import os
import httpx
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from typing import Optional
from sqlalchemy import Column, Integer, String, Boolean, Text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

# --- 1. ГЛОБАЛЬНАЯ КОНФИГУРАЦИЯ (только то, что не меняется) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("POSTGRES_PRISMA_URL")

# --- 2. ГЛОБАЛЬНОЕ ПОДКЛЮЧЕНИЕ К БД ---
Base = declarative_base()
engine = None
AsyncSessionLocal = None

try:
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена!")
    main_db_url = DATABASE_URL.split('?')[0]
    db_url_adapted = main_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(db_url_adapted)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    print("INFO: Engine и SessionLocal успешно созданы.")
except Exception as e:
    print(f"FATAL ERROR during engine setup: {e}")

# --- МОДЕЛИ ДАННЫХ ---
class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    setting_key = Column(String, unique=True)
    setting_value = Column(Text)

# --- ИНИЦИАЛИЗАЦИЯ FastAPI ---
app = FastAPI()

# --- WEB SERVER ENDPOINTS ---
@app.post("/api/webhook")
async def webhook(request: Request):
    
    # --- НАЧАЛО ЛОГИКИ БОТА (ВСЕ ОПРЕДЕЛЯЕТСЯ ВНУТРИ ФУНКЦИИ) ---
    
    # --- АСИНХРОННЫЕ ФУНКЦИИ-ПОМОЩНИКИ (локальные для этого вызова) ---
    async def get_db_setting(session: AsyncSession, key: str, default: str) -> str:
        result = await session.execute(select(Settings).filter_by(setting_key=key))
        setting = result.scalar_one_or_none()
        return setting.setting_value if setting else default

    # --- HANDLERS (локальные для этого вызова) ---
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if AsyncSessionLocal is None:
            await update.message.reply_text("⚠️ Критическая ошибка конфигурации. AsyncSessionLocal не создан.")
            return
        try:
            async with AsyncSessionLocal() as session:
                welcome_text = await get_db_setting(session, 'welcome_text', "Ошибка: не удалось прочитать приветственное сообщение.")
            await update.message.reply_text(welcome_text)
        except Exception as e:
            print(f"ERROR in /start handler: {e}")
            await update.message.reply_text(f"Произошла ошибка при работе с БД: {e}")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"Сообщение получено: {update.message.text}")

    # --- Инициализация и запуск ---
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
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

    # --- КОНЕЦ ЛОГИКИ БОТА ---

@app.get("/")
def health_check():
    return {"status": "Бот жив. Версия v11.0 (Полная инкапсуляция)."}
