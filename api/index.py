import os
import asyncio
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- НАСТРОЙКИ ---
# Загружаем переменные окружения. На Vercel вы их настроите в панели управления.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_FALLBACK_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "YOUR_ADMIN_ID")

# --- ИНИЦИАЛИЗАЦИЯ ---
# Создаем объекты бота и приложения Telegram
bot = Bot(token=TELEGRAM_TOKEN)
# webhook_url - это URL, который Vercel даст вашему проекту
# Мы его установим вручную на последнем шаге
application = Application.builder().bot(bot).build()

# Создаем веб-сервер FastAPI
app = FastAPI()

# --- ЛОГИКА ОБРАБОТЧИКОВ ---

# Обработчик команды /start (ВЕРСИЯ ДЛЯ ДИАГНОСТИКИ)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение."""
    # Эта часть должна сработать
    await update.message.reply_text(
        "Привет! Я бот-модератор. Мой скелет успешно запущен на Vercel! 🚀"
    )

    # --- Мы временно отключили проблемный блок, который падал ---
    # await context.bot.send_message(
    #     chat_id=ADMIN_CHAT_ID,
    #     text=f"Бот успешно запущен. Пользователь {update.effective_user.name} ввел команду /start."
    # )

# Обработчик всех текстовых сообщений
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просто отвечает тем же текстом (пока что)."""
    await update.message.reply_text(f"Я получил твое сообщение: '{update.message.text}'")

# --- РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ---
# Здесь мы говорим боту, на какие команды и сообщения реагировать
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))


# --- WEB-СЕРВЕР ---

@app.post("/api/webhook")
async def webhook(request: Request):
    """
    Эта функция-вебхук принимает обновления от Telegram,
    когда пользователь взаимодействует с ботом.
    """
    # Декодируем полученные данные и передаем их на обработку нашему приложению
    data = await request.json()
    update = Update.de_json(data, bot)
    await application.process_update(update)
    return {"status": "ok"}


@app.get("/")
def health_check():
    """
    Эта функция нужна, чтобы мы могли проверить, что наш сервер вообще жив,
    просто зайдя на главный URL нашего проекта.
    """
    return {"status": "Bot is alive!"}
