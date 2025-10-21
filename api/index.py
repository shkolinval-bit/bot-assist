# Файл: api/index.py

import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# --- 1. CONFIGURATION (READ FROM RENDER ENVIRONMENT) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL") 


# --- 2. DATABASE SETUP (SQLAlchemy) ---
# Базовый класс для моделей
Base = declarative_base() 

# Модель для общих настроек (settings)
class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    setting_key = Column(String, unique=True)
    setting_value = Column(Text)

# Модель для FAQ (faq)
class FAQ(Base):
    __tablename__ = 'faq'
    id = Column(Integer, primary_key=True)
    keywords = Column(Text, nullable=False)
    response_text = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)

# Инициализация подключения к Supabase
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# --- 3. BOT & APP INITIALIZATION ---
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().bot(bot).build()
app = FastAPI()


# --- 4. HANDLERS (LOGIC) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /start. Читает приветствие из БД и уведомляет админа."""
    
    # 4.1 Чтение из БД
    db = SessionLocal()
    try:
        # Пытаемся получить приветствие из таблицы settings
        welcome_setting = db.query(Settings).filter_by(setting_key='welcome_text').first()
        welcome_text = welcome_setting.setting_value if welcome_setting else "🎉 Привет! Бот запущен, БД подключена. Настроек пока нет, но мы готовы!"
    except OperationalError:
        # Если БД не отвечает или таблицы нет, используем безопасный текст
        welcome_text = "⚠️ Ошибка подключения к базе данных! Пожалуйста, проверьте переменные окружения."
    finally:
        db.close()
    
    await update.message.reply_text(welcome_text)
    
    # 4.2 Уведомление администратору (ВКЛЮЧЕНО)
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=f"Бот успешно запущен. Пользователь {update.effective_user.name} ввел команду /start."
        )
    except Exception as e:
        # Ловим ошибку, если ADMIN_CHAT_ID неверен, но не позволяем упасть всему приложению
        print(f"ОШИБКА УВЕДОМЛЕНИЯ АДМИНА: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основной обработчик для модерации и автоответов (в будущем)."""
    
    # Пока что просто отвечает эхом
    await update.message.reply_text(f"Я получил твое сообщение: '{update.message.text}'")


# --- 5. REGISTER HANDLERS ---
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)) 


# --- 6. WEB SERVER ENDPOINTS (СТАБИЛЬНАЯ ВЕРСИЯ ДЛЯ RENDER) ---

@app.post("/api/webhook")
async def webhook(request: Request):
    """Получает обновления от Telegram и обрабатывает их."""
    data = await request.json()
    
    # Стабильный режим: сразу обрабатываем обновление без лишних initialize/shutdown
    update = Update.de_json(data, bot)
    await application.process_update(update)
    
    return {"status": "ok"}

@app.get("/")
def health_check():
    """Проверка здоровья."""
    return {"status": "Bot is alive and ready for action!"}
