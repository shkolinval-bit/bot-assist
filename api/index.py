# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v5.2 - ПОЛНОСТЬЮ АСИНХРОННАЯ)

import os
import httpx
import traceback
from fastapi import FastAPI, Request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
from typing import Optional
from sqlalchemy import Column, Integer, String, Boolean, Text, select, inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
HUGGING_FACE_TOKEN = os.getenv("HUGGING_FACE_TOKEN")
DATABASE_URL = os.getenv("POSTGRES_PRISMA_URL")
HUGGING_FACE_MODEL_URL = "https://api-inference.huggingface.co/models/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-fein-tuned"

# Константы
MOD_THRESHOLD_DEFAULT = 0.85
STATE_AWAITING_NEW_THRESHOLD = 'AWAITING_NEW_THRESHOLD'
engine = None
AsyncSessionLocal = None

# --- 2. DATABASE SETUP ---
Base = declarative_base()

try:
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена!")

    main_db_url = DATABASE_URL.split('?')[0]
    db_url_adapted = main_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url_adapted)
    
except Exception as e:
    print(f"FATAL ERROR during initial engine setup: {e}")

# --- МОДЕЛИ ДАННЫХ ---
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

# --- ИНИЦИАЛИЗАЦИЯ БОТА И FastAPI ---
application: Optional[Application] = None
try:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
except Exception as e:
    print(f"ERROR: Не удалось создать Application: {e}")

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    global AsyncSessionLocal
    if engine is None:
        print("Engine не был инициализирован, создание таблиц пропущено.")
        return
        
    try:
        async with engine.connect() as conn:
            inspector = inspect(conn)
            required_tables = {"settings", "faq"}
            existing_tables = await conn.run_sync(inspector.get_table_names)
            
            print(f"INFO: Обнаружены таблицы в БД: {existing_tables}")
            
            if not required_tables.issubset(existing_tables):
                print("INFO: Не все таблицы существуют. Запускаю создание...")
                async with engine.begin() as tx_conn:
                    await tx_conn.run_sync(Base.metadata.create_all)
                print("INFO: Команда создания таблиц выполнена.")
            else:
                print("INFO: Все необходимые таблицы уже существуют.")

        AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        print("INFO: Настройка базы данных завершена успешно.")

    except Exception as e:
        AsyncSessionLocal = None
        print(f"FATAL ERROR during table creation: {e}")
        error_trace = traceback.format_exc()
        error_message = f"🔴 КРИТИЧЕСКАЯ ОШИБКА БОТА 🔴\n\nНе удалось создать/проверить таблицы в БД.\n\nОшибка: {e}\n\nТрассировка:\n{error_trace}"
        if TELEGRAM_TOKEN and ADMIN_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": ADMIN_CHAT_ID, "text": error_message[:4096]}
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload)

# --- АСИНХРОННЫЕ ФУНКЦИИ-ПОМОЩНИКИ ---
async def classify_text_huggingface(text: str, labels: list) -> Optional[dict]:
    if not HUGGING_FACE_TOKEN: return None
    headers = {"Authorization": f"Bearer {HUGGING_FACE_TOKEN}"}
    payload = {"inputs": text, "parameters": {"candidate_labels": labels, "multi_label": True}}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(HUGGING_FACE_MODEL_URL, headers=headers, json=payload, timeout=10.0)
            return response.json() if response.status_code == 200 else None
        except httpx.RequestError: return None

async def get_db_setting(key: str, default: str) -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Settings).filter_by(setting_key=key))
        setting = result.scalar_one_or_none()
        return setting.setting_value if setting else default

async def set_db_setting(key: str, value: str):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(select(Settings).filter_by(setting_key=key))
            setting = result.scalar_one_or_none()
            if setting:
                setting.setting_value = value
            else:
                session.add(Settings(setting_key=key, setting_value=value))

async def find_faq_response(message_text: str) -> str | None:
    if AsyncSessionLocal is None: return None
    async with AsyncSessionLocal() as session:
        message_words = set(message_text.lower().split())
        result = await session.execute(select(FAQ).filter(FAQ.enabled == True))
        active_faqs = result.scalars().all()
        for faq in active_faqs:
            faq_keywords = set(faq.keywords.lower().split(','))
            if any(word in message_words for word in faq_keywords):
                return faq.response_text
        return None

async def analyze_for_scam(text_to_analyze: str) -> bool:
    if not GEMINI_API_KEY: return False
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        prompt = (f"Проанализируй на финансовый спам, мошенничество или фишинг. Ответь только ДА или НЕТ.\n\nСообщение: \"{text_to_analyze}\"")
        response = await model.generate_content_async(prompt)
        return response.text.strip().upper() == 'ДА'
    except Exception as e:
        print(f"ERROR: Ошибка вызова Gemini API: {e}")
        return False

async def generate_response(user_prompt: str) -> str:
    if not GEMINI_API_KEY: return "Ключ Gemini API не настроен."
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        response = await model.generate_content_async(user_prompt)
        return response.text
    except Exception as e:
        print(f"ERROR: Ошибка вызова Gemini API: {e}")
        return "Произошла ошибка при обращении к ИИ."

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AsyncSessionLocal is None:
        await update.message.reply_text("⚠️ Ошибка подключения к базе данных. Администратор уведомлен.")
        return
    try:
        welcome_text = await get_db_setting('welcome_text', "🎉 Бот запущен и подключен к базе данных! Используйте /admin для настроек.")
        await update.message.reply_text(welcome_text)
    except Exception as e:
        print(f"ERROR in /start handler: {e}")
        await update.message.reply_text(f"Произошла ошибка при получении настроек: {e}")

async def add_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Эта команда доступна только администратору.")
        return
    if AsyncSessionLocal is None:
        await update.message.reply_text("⚠️ База данных не инициализирована.")
        return
    if len(context.args) < 1 or ';' not in " ".join(context.args):
        await update.message.reply_text("❌ Использование: /addfaq <ключи,через,запятую>; <текст ответа>")
        return
    try:
        full_text = " ".join(context.args)
        keywords_part, response_part = full_text.split(';', 1)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                new_faq = FAQ(keywords=keywords_part.strip().lower(), response_text=response_part.strip(), enabled=True)
                session.add(new_faq)
        await update.message.reply_text(f"✅ Новый FAQ сохранен!\nКлючи: {keywords_part.strip().lower()}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка сохранения FAQ: {e}")

async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    mention_username = f"@{context.bot.username}"
    clean_text = message_text.replace(mention_username, "", 1).strip()
    if not clean_text:
        await update.message.reply_text("Я здесь. Спрашивайте! 🤖")
        return
    if await analyze_for_scam(clean_text):
        await update.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Вопрос удален ИИ. Причина: Мошенничество/спам.")
        return
    await update.message.reply_text("Думаю... (использую Gemini)")
    response = await generate_response(clean_text)
    await update.message.reply_text(response)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    if await analyze_for_scam(message_text):
        await update.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Сообщение удалено ИИ (Gemini). Причина: Обнаружено мошенничество/спам.")
        return

    mod_threshold = MOD_THRESHOLD_DEFAULT
    if AsyncSessionLocal:
        try:
            mod_threshold = float(await get_db_setting('mod_threshold', str(MOD_THRESHOLD_DEFAULT)))
        except Exception:
            pass # Игнорируем ошибку, если настройка не найдена, используем дефолт

    candidate_labels = ["токсичность", "предложение работы", "реклама", "финансовый спам"]
    results = await classify_text_huggingface(message_text, candidate_labels)
    if results and results.get('labels') and results.get('scores'):
        best_label, best_score = results['labels'][0], results['scores'][0]
        if (best_label in ["токсичность", "реклама"]) and best_score > mod_threshold:
            await update.message.delete()
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Сообщение удалено ИИ. Причина: {best_label} (Уверенность: {best_score:.2%}).")
            return

    faq_answer = await find_faq_response(message_text)
    if faq_answer:
        await update.message.reply_text(faq_answer)
        return

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Доступ запрещен.")
        return
    context.user_data['state'] = None
    keyboard = [[InlineKeyboardButton("⚙️ Настроить порог модерации", callback_data='admin_moderation')],]
    await update.message.reply_text('Меню администратора:', reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'admin_moderation':
        if AsyncSessionLocal is None:
            await query.message.edit_text("⚠️ База данных не инициализирована.")
            return
        try:
            current_threshold = await get_db_setting('mod_threshold', str(MOD_THRESHOLD_DEFAULT))
            text = f"Текущий порог модерации: **{current_threshold}**\n\nВведите новое значение (от 0.00 до 1.00):"
            context.user_data['state'] = STATE_AWAITING_NEW_THRESHOLD
            await query.message.edit_text(text, parse_mode='Markdown')
        except Exception as e:
            await query.message.edit_text(f"Ошибка получения настроек: {e}")

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == STATE_AWAITING_NEW_THRESHOLD:
        try:
            float_value = float(update.message.text)
            if not (0.0 <= float_value <= 1.0): raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Ошибка: Введите число от 0.00 до 1.00.")
            return
        try:
            await set_db_setting('mod_threshold', str(float_value))
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Порог модерации обновлен до: **{float_value}**.", parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка БД при сохранении: {e}")

# --- REGISTER HANDLERS ---
if application:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addfaq", add_faq))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern='^admin_'))
    application.add_handler(MessageHandler(filters.TEXT & filters.User(user_id=int(ADMIN_CHAT_ID)) & ~filters.COMMAND, handle_admin_input))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity("mention"), handle_mention))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
else:
    print("ERROR: Application не был инициализирован.")

# --- WEB SERVER ENDPOINTS ---
@app.post("/api/webhook")
async def webhook(request: Request):
    if not application: return {"status": "error", "message": "Bot not initialized"}
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
    return {"status": "Бот жив и готов к работе на Vercel!"}
