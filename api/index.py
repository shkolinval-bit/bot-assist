# Файл: api/index.py

import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().bot(bot).build()
app = FastAPI()

# --- ЛОГИКА ОБРАБОТЧИКОВ ---

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение."""
    await update.message.reply_text(
        "Привет! Я бот-модератор. Базовая версия работает! 🚀"
    )
    # Проблемная часть временно отключена для стабильности
    # await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="Бот запущен.")

# Обработчик всех текстовых сообщений
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просто отвечает тем же текстом."""
    await update.message.reply_text(f"Я получил твое сообщение: '{update.message.text}'")

# --- РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ---
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

# --- WEB-СЕРВЕР ---

@app.post("/api/webhook")
async def webhook(request: Request):
    """
    Эта функция-вебхук принимает обновления от Telegram, 
    инициализирует приложение, обрабатывает обновление и корректно завершается.
    """
    # Декодируем полученные данные
    data = await request.json()
    
    # !!! КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: ИНИЦИАЛИЗАЦИЯ !!!
    # Мы говорим приложению, что нужно подготовиться к работе.
    await application.initialize() 
    # !!! КОНЕЦ ИСПРАВЛЕНИЯ !!!

    update = Update.de_json(data, bot)
    await application.process_update(update)
    
    # Корректно завершаем приложение, освобождая ресурсы.
    await application.shutdown() 
    
    return {"status": "ok"}
