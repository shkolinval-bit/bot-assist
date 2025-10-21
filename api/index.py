# Файл: api/index.py

import os
import asyncio
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base

# --- НАСТРОЙКИ (Берутся из переменных окружения Render) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL") 


# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (ORM) ---

# Объявляем базовый класс для моделей
Base = declarative_base() 

# Создаем модель для таблицы settings (должна быть создана SQL-кодом)
class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    setting_key = Column(String, unique=True)
    setting_value = Column(Text)

# Создаем модель для таблицы faq (должна быть создана SQL-кодом)
class FAQ(Base):
    __tablename__ = 'faq'
    id = Column(Integer, primary_key=True)
    keywords = Column(Text, nullable=False)
    response_text = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)


# Создание движка и сессии (подключение к Supabase)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().bot(bot).build()
app = FastAPI()


# --- ЛОГИКА ОБРАБОТЧИКОВ ---

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет приветственное сообщение и уведомление админу.
    (Теперь без кода, который падал, так как ADMIN_CHAT_ID теперь число!)
    """
    db = SessionLocal()
    try:
        # Пример чтения из БД
        welcome_setting = db.query(Settings).filter_by(setting_key='welcome_text').first()
        welcome_text = welcome_setting.setting_value if welcome_setting else "Привет! Я бот-модератор. База данных подключена и ждет настроек!"
    finally:
        db.close()
    
    await update.message.reply_text(welcome_text)
    
    # Отправка уведомления администратору (ВКЛЮЧЕНО)
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, # Здесь теперь должно быть корректное число
            text=f"Бот успешно запущен. Пользователь {update.effective_user.name} ввел команду /start."
        )
    except Exception as e:
        print(f"ОШИБКА УВЕДОМЛЕНИЯ АДМИНА: {e}")
        # Если здесь будет ошибка, скорее всего, ADMIN_CHAT_ID все еще не число или неверное ID.


# Обработчик всех текстовых сообщений (пока только echo)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Основной обработчик для модерации и автоответов.
    """
    # Здесь в будущем будет логика проверки:
    # 1. Сначала: Проверка на капс, злоумышленников (п. 2, 4 ТЗ)
    # 2. Затем: Проверка на FAQ (п. 3 ТЗ)
    
    # Пока что просто отвечает эхом
    await update.message.reply_text(f"Я получил твое сообщение: '{update.message.text}'")


# --- РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ---
application.add_handler(CommandHandler("start", start))
# Обработчик сообщений, включая все текстовые, которые не являются командами
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)) 


# --- WEB-СЕРВЕР ---

@app.post("/api/webhook")
async def webhook(request: Request):
    data = await request.json()
    
    await application.initialize() 

    update = Update.de_json(data, bot)
    await application.process_update(update)
    
    await application.shutdown() 
    
    return {"status": "ok"}

@app.get("/")
def health_check():
    # Теперь здесь не нужно ничего вызывать. Проверка подключения произошла при запуске.
    return {"status": "Bot is alive and ready for action!"}
