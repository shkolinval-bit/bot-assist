# Файл: api/index.py

import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# --- ИНИЦИАЛИЗАЦИЯ НЕЙРОСЕТИ ---
try:
    from transformers import pipeline
    # NOTE: Модель SkolkovoInstitute/russian_toxicity_classifier отлично подходит для русского языка.
    text_classifier = pipeline(
        "text-classification",
        model="SkolkovoInstitute/russian_toxicity_classifier",
        tokenizer="SkolkovoInstitute/russian_toxicity_classifier"
    )
    print("INFO: Нейросеть успешно загружена и готова к работе.")
except ImportError:
    print("WARNING: Библиотеки для нейросети не установлены. Модерация отключена.")
    text_classifier = None
except Exception as e:
    # Может быть ошибка при скачивании модели или нехватка памяти при cold start
    print(f"ERROR: Ошибка загрузки нейросети: {e}")
    text_classifier = None


# --- 1. CONFIGURATION (READ FROM RENDER ENVIRONMENT) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL") 


# --- 2. DATABASE SETUP (SQLAlchemy) ---
Base = declarative_base() 

# Модель для общих настроек (settings) - создана вручную в Supabase
class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    setting_key = Column(String, unique=True)
    setting_value = Column(Text)

# Модель для FAQ (faq) - создана вручную в Supabase
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
        welcome_setting = db.query(Settings).filter_by(setting_key='welcome_text').first()
        welcome_text = welcome_setting.setting_value if welcome_setting else "🎉 Привет! Бот запущен, БД подключена, но настроек пока нет."
    except OperationalError:
        welcome_text = "⚠️ Ошибка подключения к базе данных! Проверьте DATABASE_URL."
    finally:
        db.close()
    
    await update.message.reply_text(welcome_text)
    
    # 4.2 Уведомление администратору
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=f"Бот успешно запущен. Пользователь {update.effective_user.name} ввел команду /start."
        )
    except Exception as e:
        print(f"ОШИБКА УВЕДОМЛЕНИЯ АДМИНА: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Основной обработчик:
    1. Модерация (нейросеть)
    2. Автоответы (FAQ)
    """
    message_text = update.message.text
    
    # 1. МОДУЛЬ НЕЙРОСЕТЕВОЙ МОДЕРАЦИИ (Пункт 2 ТЗ)
    if text_classifier:
        # NOTE: Порог в 0.85 означает "высокую уверенность"
        results = text_classifier(message_text)[0]
        label = results['label']
        score = results['score']
        
        # Если модель уверена, что сообщение токсично
        if label == 'toxic' and score > 0.85:
            try:
                # Действие: Удаляем сообщение
                await update.message.delete()
                warning = f"❌ Сообщение удалено AI-модератором (Уверенность: {score:.2%})."
                
                # Отправляем предупреждение пользователю
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, 
                    text=warning,
                    reply_to_message_id=update.message.message_id # Ссылка на удаленное сообщение
                )
                return # Прекращаем обработку
            except Exception as e:
                # Если у бота нет прав на удаление
                print(f"ERROR: Не удалось выполнить модерационное действие: {e}")
                
    
    # 2. Модуль FAQ (в будущем будет здесь)
    
    # 3. Базовый ответ (если модерация пройдена)
    await update.message.reply_text(f"Я получил твое сообщение: '{message_text}'")


# --- 5. REGISTER HANDLERS ---
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)) 


# --- 6. WEB SERVER ENDPOINTS (СТАБИЛЬНАЯ ВЕРСИЯ) ---

@app.post("/api/webhook")
async def webhook(request: Request):
    """Получает обновления от Telegram и обрабатывает их."""
    data = await request.json()
    
    update = Update.de_json(data, bot)
    await application.process_update(update)
    
    return {"status": "ok"}

@app.get("/")
def health_check():
    """Проверка здоровья."""
    return {"status": "Bot is alive and ready for action!"}
