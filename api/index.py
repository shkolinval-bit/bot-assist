# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v20+)

import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
# !!! ИСПРАВЛЕНИЕ: Используем Application и ApplicationBuilder для v20+ !!!
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# --- 1. CONFIGURATION (READ FROM RENDER ENVIRONMENT) ---
# Эти переменные должны быть настроены в вашей среде Render
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# --- 2. ИНИЦИАЛИЗАЦИЯ НЕЙРОСЕТИ (ГИБРИДНАЯ СИСТЕМА) ---
try:
    from transformers import pipeline
    # Zero-Shot для быстрой классификации спама, токсичности и рекламы
    text_classifier = pipeline(
        "zero-shot-classification",
        model="s-nlp/ru-mtl-zero-shot-public"
    )
    print("INFO: Zero-Shot классификатор загружен.")
except Exception as e:
    print(f"ERROR: Ошибка загрузки Zero-Shot: {e}")
    text_classifier = None


# --- 3. DATABASE SETUP (SQLAlchemy) ---
Base = declarative_base()
class Settings(Base):
    __tablename__ = 'settings'
    id = Column(Integer, primary_key=True)
    setting_key = Column(String, unique=True)
    setting_value = Column(Text)

class FAQ(Base):
    __tablename__ = 'faq'
    id = Column(Integer, primary_key=True)
    keywords = Column(Text, nullable=False)
    response_text = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)

try:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    print("INFO: База данных настроена.")
except Exception as e:
    print(f"ERROR: Ошибка настройки базы данных: {e}")
    SessionLocal = None


# --- 4. BOT & APP INITIALIZATION (ФИКС v20+) ---
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
async def analyze_for_scam(text_to_analyze: str) -> bool:
    """Отправляет текст в Gemini API для глубокого анализа мошенничества."""
    if not GEMINI_API_KEY:
        return False
        
    try:
        # Примечание: Для асинхронного использования 'google-genai' может потребоваться 
        # использование асинхронных оберток, но сохраняем код пользователя.
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = (
            "Ты — строгий модератор. Проанализируй сообщение. "
            "Если оно является явным финансовым спамом, мошенническим предложением о работе, "
            "или фишингом, ответь ТОЛЬКО одним словом: ДА. В противном случае ответь ТОЛЬКО одним словом: НЕТ.\n\n"
            f"Сообщение: \"{text_to_analyze}\""
        )
        
        # В рабочем коде может потребоваться асинхронный вызов, 
        # но используем функцию, как она была в вашем файле:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text.strip().upper() == 'ДА'
        
    except Exception as e:
        print(f"ERROR: Ошибка вызова Gemini API: {e}")
        return False


# --- 6. HANDLERS (ЛОГИКА) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /start."""
    db = SessionLocal()
    try:
        welcome_setting = db.query(Settings).filter_by(setting_key='welcome_text').first()
        welcome_text = welcome_setting.setting_value if welcome_setting else "🎉 Привет! Бот запущен, БД подключена. Настроек пока нет, но мы готовы!"
    except OperationalError:
        welcome_text = "⚠️ Ошибка подключения к базе данных! Проверьте DATABASE_URL."
    finally:
        db.close()
    
    await update.message.reply_text(welcome_text)
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"Бот успешно запущен. Пользователь {update.effective_user.name} ввел команду /start."
        )
    except Exception as e:
        print(f"ОШИБКА УВЕДОМЛЕНИЯ АДМИНА: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основной обработчик: 1. Gemini, 2. Zero-Shot, 3. Ответ."""
    message_text = update.message.text
    
    # 1. МОДУЛЬ GEMINI (Глубокая проверка)
    if await analyze_for_scam(message_text):
        await update.message.delete()
        warning = "❌ Сообщение удалено AI (Gemini). Причина: Обнаружено мошенничество/спам."
        await context.bot.send_message(chat_id=update.effective_chat.id, text=warning)
        return
        
    # 2. МОДУЛЬ ZERO-SHOT (Базовая проверка на токсичность/рекламу)
    if text_classifier:
        candidate_labels = ["токсичность", "предложение работы", "реклама", "финансовый спам"]
        results = text_classifier(message_text, candidate_labels, multi_label=True)
        best_label = results['labels'][0]
        best_score = results['scores'][0]
        
        if (best_label in ["токсичность", "реклама"]) and best_score > 0.85:
            await update.message.delete()
            warning = f"❌ Сообщение удалено AI. Причина: {best_label} (Уверенность: {best_score:.2%})."
            await context.bot.send_message(chat_id=update.effective_chat.id, text=warning)
            return

    # 3. Базовый ответ
    await update.message.reply_text(f"Я получил твое сообщение: '{message_text}'")


# --- 7. REGISTER HANDLERS ---
# !!! ИСПРАВЛЕНИЕ: Регистрируем обработчики через 'application' !!!
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
        
        # !!! КЛЮЧЕВОЙ ФИКС ДЛЯ RuntimeError: Инициализация при каждом вызове !!!
        await application.initialize() 
        await application.process_update(update) 
        await application.shutdown() 
        
        return {"status": "ok"}
        
    except Exception as e:
        print(f"ERROR: Ошибка в обработчике вебхука: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/")
def health_check():
    """Проверка здоровья."""
    return {"status": "Bot is alive and ready for action!"}
