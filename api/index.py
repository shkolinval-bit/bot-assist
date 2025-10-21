# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v13.0 - ПОЛНОСТЬЮ СИНХРОННАЯ)

import os
import httpx
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import sessionmaker, declarative_base

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("POSTGRES_PRISMA_URL")

# --- 2. DATABASE SETUP (СИНХРОННОЕ) ---
Base = declarative_base()
engine = None
SessionLocal = None

try:
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена!")
    
    # Убираем параметры и адаптируем для pg8000
    main_db_url = DATABASE_URL.split('?')[0]
    db_url_adapted = main_db_url.replace("postgres://", "postgresql+pg8000://", 1)
    
    # Используем обычный create_engine
    engine = create_engine(db_url_adapted)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Так как мы не в async-режиме, можно безопасно создать таблицы здесь
    # checkfirst=True не будет создавать таблицы, если они уже есть
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

# --- ИНИЦИАЛИЗАЦИЯ FastAPI ---
app = FastAPI()

# --- СИНХРОННЫЕ ФУНКЦИИ-ПОМОЩНИКИ ---
def get_db_setting(session, key: str, default: str) -> str:
    setting = session.query(Settings).filter_by(setting_key=key).first()
    return setting.setting_value if setting else default

# --- HANDLERS (теперь это обычные синхронные функции) ---
# Важно: python-telegram-bot v20+ умеет работать с синхронными хендлерами, запуская их в отдельном потоке.
def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if SessionLocal is None:
        update.message.reply_text("⚠️ Критическая ошибка конфигурации. SessionLocal не создан.")
        return
        
    session = SessionLocal()
    try:
        welcome_text = get_db_setting(session, 'welcome_text', "Ошибка: не удалось прочитать приветственное сообщение.")
        update.message.reply_text(welcome_text)
    except Exception as e:
        print(f"ERROR in /start handler: {e}")
        update.message.reply_text(f"Произошла ошибка при работе с БД: {e}")
    finally:
        session.close()

def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update.message.reply_text(f"Сообщение получено: {update.message.text}")

# --- WEB SERVER ENDPOINTS ---
@app.post("/api/webhook")
async def webhook(request: Request):
    
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Регистрируем наши синхронные хендлеры
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

@app.get("/")
def health_check():
    return {"status": "Бот жив. Версия v13.0 (Полностью синхронная)."}
