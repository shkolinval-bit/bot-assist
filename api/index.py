# Файл: api/index.py (ИСПРАВЛЕННАЯ ВЕРСИЯ v20+)

import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
# !!! ИЗМЕНЕНИЕ: Убираем Dispatcher, импортируем Application и ApplicationBuilder !!!
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# ... (секции 1, 2, 3 остаются без изменений) ...


# --- 4. BOT & APP INITIALIZATION (ФИКС v20+) ---
# !!! ИЗМЕНЕНИЕ: Используем ApplicationBuilder для создания приложения !!!
# 'application' теперь содержит и бота, и диспетчер
try:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot = application.bot  # Можно получить бота из 'application', если он нужен отдельно
    print("INFO: Application (v20+) инициализирован.")
except Exception as e:
    print(f"ERROR: Не удалось создать Application: {e}")
    # Если приложение не создалось, FastAPI все равно должен запуститься, 
    # но вебхук будет падать с ошибкой (что ожидаемо)
    application = None 
    bot = None # Добавляем bot = None для ясности

app = FastAPI()


# --- 5. ФУНКЦИЯ: АНАЛИЗ СПАМА ЧЕРЕЗ GEMINI (LLM) ---
# ... (секция 5 остается без изменений) ...


# --- 6. HANDLERS (ЛОГИКА) ---
# ... (секция 6 остается без изменений, т.к. сами функции 'start' и 'handle_message' корректны) ...


# --- 7. REGISTER HANDLERS ---
# !!! ИЗМЕНЕНИЕ: Регистрируем обработчики через 'application', а не 'dp' !!!
if application:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
else:
    print("ERROR: Application не инициализирован, хэндлеры не добавлены.")


# --- 8. WEB SERVER ENDPOINTS (СТАБИЛЬНАЯ ВЕРСИЯ v20+) ---

@app.post("/api/webhook")
async def webhook(request: Request):
    """
    Обрабатывает обновления через Application.
    Это правильный паттерн для serverless окружения (v20+).
    """
    if not application:
        print("ERROR: Вебхук вызван, но Application не создан.")
        return {"status": "error", "message": "Bot not initialized"}

    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        
        # !!! КЛЮЧЕВОЙ ФИКС ДЛЯ RuntimeError !!!
        # 1. Инициализируем приложение (для этого вызова)
        await application.initialize() 
        # 2. Обрабатываем обновление
        await application.process_update(update) 
        # 3. Закрываем сессию приложения
        await application.shutdown() 
        
        return {"status": "ok"}
        
    except Exception as e:
        print(f"ERROR: Ошибка в обработчике вебхука: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/")
def health_check():
    """Проверка здоровья."""
    return {"status": "Bot is alive and ready for action!"}
