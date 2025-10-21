# Файл: api/index.py (ФИНАЛЬНАЯ СТАБИЛЬНАЯ ВЕРСИЯ С GEMINI)

import os
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, filters, ContextTypes
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# --- 1. CONFIGURATION (READ FROM RENDER ENVIRONMENT) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL") 
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # <--- Ключ для LLM


# --- 2. ИНИЦИАЛИЗАЦИЯ НЕЙРОСЕТИ (Toxicity) ---
try:
    # Оставляем Zero-Shot для базовой модерации (бесплатный, быстрый уровень)
    from transformers import pipeline
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

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# --- 4. BOT & APP INITIALIZATION (ФИКС ОШИБКИ) ---
bot = Bot(token=TELEGRAM_TOKEN)
# !!! ФИКС: Используем Dispatcher, чтобы избежать RuntimeError !!!
dp = Dispatcher(bot, None) 
app = FastAPI()


# --- 5. НОВАЯ ФУНКЦИЯ: АНАЛИЗ СПАМА ЧЕРЕЗ GEMINI ---
async def analyze_for_scam(text_to_analyze: str) -> bool:
    """Отправляет текст в Gemini API для классификации мошенничества."""
    if not GEMINI_API_KEY:
        print("WARNING: GEMINI_API_KEY не установлен. Пропускаю проверку LLM.")
        return False
        
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = (
            "Ты — строгий модератор. Проанализируй следующее сообщение. "
            "Если оно является явным финансовым спамом, мошенническим предложением о работе, "
            "или фишингом, ответь ТОЛЬКО одним словом: ДА. В противном случае ответь ТОЛЬКО одним словом: НЕТ.\n\n"
            f"Сообщение: \"{text_to_analyze}\""
        )
        
        # Используем асинхронный вызов LLM
        response = await client.models.generate_content(
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
    # (Логика с БД для получения приветствия)
    # ... (код start опущен для краткости, он остался таким же) ...
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
    
    # 1. МОДУЛЬ GEMINI (Глубокая проверка на спам/работу)
    if await analyze_for_scam(message_text):
        await update.message.delete()
        warning = "❌ Сообщение удалено AI (Gemini). Причина: Обнаружено мошенничество/спам."
        await context.bot.send_message(chat_id=update.effective_chat.id, text=warning)
        return

    # 2. МОДУЛЬ ZERO-SHOT (Базовая проверка на токсичность/рекламу)
    if text_classifier:
        candidate_labels = ["финансовый спам", "предложение работы", "мошенничество", "токсичность"]
        results = text_classifier(message_text, candidate_labels, multi_label=True)
        best_label = results['labels'][0]
        best_score = results['scores'][0]
        
        if (best_label in ["токсичность", "предложение работы"]) and best_score > 0.85:
            await update.message.delete()
            warning = f"❌ Сообщение удалено AI. Причина: {best_label} (Уверенность: {best_score:.2%})."
            await context.bot.send_message(chat_id=update.effective_chat.id, text=warning)
            return

    # 3. Базовый ответ (если модерация пройдена)
    await update.message.reply_text(f"Я получил твое сообщение: '{message_text}'")


# --- 7. REGISTER HANDLERS ---
# !!! ФИКС: Регистрируем обработчики через dp !!!
dp.add_handler(CommandHandler("start", start))
dp.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)) 


# --- 8. WEB SERVER ENDPOINTS (СТАБИЛЬНАЯ ВЕРСИЯ) ---

@app.post("/api/webhook")
async def webhook(request: Request):
    """Обрабатывает обновления через Dispatcher."""
    data = await request.json()
    
    update = Update.de_json(data, bot)
    await dp.process_update(update) # Прямая передача в Dispatcher
    
    return {"status": "ok"}

@app.get("/")
def health_check():
    """Проверка здоровья."""
    return {"status": "Bot is alive and ready for action!"}
