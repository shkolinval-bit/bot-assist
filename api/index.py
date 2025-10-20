# Файл: api/index.py (Новая версия с подключением к БД)

import os
import asyncio
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, declarative_base

# --- НАСТРОЙКИ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
# Новая переменная для подключения к БД
DATABASE_URL = os.getenv("DATABASE_URL") 


# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (ORM) ---

# Объявляем базовый класс для моделей
Base = declarative_base() 
# Создаем модель для хранения настроек (Пока что только один пример)
class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    setting_key = Column(String, unique=True) # Ключ настройки (e.g., 'welcome_text')
    setting_value = Column(String)            # Значение (e.g., 'Приветственный текст')

# Создание движка и сессии (будет работать только если DATABASE_URL установлен)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Функция для создания таблиц (вызывается при старте)
def initialize_db():
    Base.metadata.create_all(bind=engine)

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
bot = Bot(token=TELEGRAM_TOKEN)
application = Application.builder().bot(bot).build()
app = FastAPI()

# --- ЛОГИКА ОБРАБОТЧИКОВ ---

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Теперь мы будем читать данные из БД (пример)
    db = SessionLocal()
    try:
        welcome_text = db.query(Settings).filter_by(setting_key='welcome_text').first()
        text_to_send = welcome_text.setting_value if welcome_text else "Привет! Это приветствие по умолчанию."
    finally:
        db.close()
        
    await update.message.reply_text(text_to_send)
    
    # Этот блок все еще вызовет 500 ошибку, если ADMIN_CHAT_ID не число!
    # Если вы хотите, чтобы бот работал без сбоев, пока не настроите админ-чат, 
    # держите его закомментированным, или убедитесь, что ADMIN_CHAT_ID — это ТОЛЬКО ЦИФРЫ.
    # await context.bot.send_message(
    #     chat_id=ADMIN_CHAT_ID,
    #     text=f"Бот запущен. Пользователь {update.effective_user.name} ввел команду /start."
    # )

# Обработчик всех текстовых сообщений
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Я получил твое сообщение: '{update.message.text}'")

# --- РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ---
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))


# --- WEB-СЕРВЕР ---

@app.on_event("startup")
def on_startup():
    # Вызываем создание таблиц при запуске (только при первом развертывании)
    initialize_db()

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
    return {"status": "Bot is alive and connected to DB!"}
