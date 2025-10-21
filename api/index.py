# Файл: api/index.py (ЛЕГКАЯ ВЕРСИЯ ДЛЯ VERCEL v21+)

import os
import httpx  # --- ИЗМЕНЕНИЕ: Используем httpx для асинхронных API-запросов ---
from fastapi import FastAPI, Request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# --- ИЗМЕНЕНИЕ: Добавляем токен для Hugging Face ---
HUGGING_FACE_TOKEN = os.getenv("HUGGING_FACE_TOKEN")
HUGGING_FACE_MODEL_URL = "https://api-inference.huggingface.co/models/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-fein-tuned"

# Константы для логики и состояний
MOD_THRESHOLD_DEFAULT = 0.85
STATE_AWAITING_NEW_THRESHOLD = 'AWAITING_NEW_THRESHOLD'

# --- 2. ИНИЦИАЛИЗАЦИЯ НЕЙРОСЕТИ -> ЗАМЕНЕНА НА API-КЛИЕНТ ---
# Локальный классификатор удален для совместимости с Vercel

async def classify_text_huggingface(text: str, labels: list) -> Optional[dict]:
    """
    --- ИЗМЕНЕНИЕ: Новая функция для классификации текста через Hugging Face API. ---
    Отправляет текст на API и возвращает результат в формате, похожем на старый pipeline.
    """
    if not HUGGING_FACE_TOKEN:
        print("WARNING: Hugging Face token not configured.")
        return None

    headers = {"Authorization": f"Bearer {HUGGING_FACE_TOKEN}"}
    payload = {
        "inputs": text,
        "parameters": {"candidate_labels": labels, "multi_label": True},
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(HUGGING_FACE_MODEL_URL, headers=headers, json=payload, timeout=10.0)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"ERROR: Hugging Face API error {response.status_code}: {response.text}")
                return None
        except httpx.RequestError as e:
            print(f"ERROR: HTTPX request to Hugging Face failed: {e}")
            return None


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

SessionLocal = None
try:
    # --- ОТЛАДОЧНЫЙ КОД ---
    db_url_from_env = os.getenv("DATABASE_URL")
    print(f"DEBUG: Проверяю переменную окружения DATABASE_URL.")
    if not db_url_from_env:
        raise ValueError("Переменная DATABASE_URL не найдена! Проверьте настройки Vercel.")
    print(f"DEBUG: Переменная DATABASE_URL найдена.")
    # --- КОНЕЦ ОТЛАДОЧНОГО КОДА ---

    # Адаптация строки подключения для Vercel Postgres и asyncpg
    if "postgres://" in db_url_from_env:
        db_url_adapted = db_url_from_env.replace("postgres://", "postgresql+asyncpg://", 1)
        engine = create_engine(db_url_adapted)
        print(f"DEBUG: Engine создан с драйвером asyncpg.")
    else:
        # Этот блок на всякий случай, если формат URL другой
        engine = create_engine(db_url_from_env)
        print(f"DEBUG: Engine создан со стандартным драйвером.")

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    print("INFO: База данных успешно настроена и таблицы созданы/проверены.")
except Exception as e:
    # Эта ошибка теперь будет более информативной
    print(f"FATAL ERROR: Критическая ошибка настройки базы данных: {e}. SessionLocal не инициализирован.")
    SessionLocal = None


# --- 4. BOT & APP INITIALIZATION ---
application: Optional[Application] = None
bot: Optional[Bot] = None
try:
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot = application.bot
    print("INFO: Application (v20+) инициализирован.")
except Exception as e:
    print(f"ERROR: Не удалось создать Application: {e}")
    application = None
    bot = None

app = FastAPI()


# --- 5. ФУНКЦИИ-ПОМОЩНИКИ ---

def get_db_setting(db_session, key: str, default: str) -> str:
    setting = db_session.query(Settings).filter_by(setting_key=key).first()
    return setting.setting_value if setting else default

def set_db_setting(db_session, key: str, value: str):
    setting = db_session.query(Settings).filter_by(setting_key=key).first()
    if setting:
        setting.setting_value = value
    else:
        db_session.add(Settings(setting_key=key, setting_value=value))
    db_session.commit()

async def analyze_for_scam(text_to_analyze: str) -> bool:
    if not GEMINI_API_KEY: return False
    try:
        from google import genai
        # --- ИЗМЕНЕНИЕ: Упрощенная инициализация ---
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        prompt = (
            "Ты — строгий модератор. Проанализируй сообщение. "
            "Если оно является явным финансовым спамом, мошенническим предложением о работе, "
            "или фишингом, ответь ТОЛЬКО одним словом: ДА. В противном случае ответь ТОЛЬКО одним словом: НЕТ.\n\n"
            f"Сообщение: \"{text_to_analyze}\""
        )
        response = await model.generate_content_async(prompt)
        return response.text.strip().upper() == 'ДА'
    except Exception as e:
        print(f"ERROR: Ошибка вызова Gemini API: {e}")
        return False

async def generate_response(user_prompt: str) -> str:
    if not GEMINI_API_KEY: return "Извините, Gemini API-ключ не настроен."
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        response = await model.generate_content_async(user_prompt)
        return response.text
    except Exception as e:
        print(f"ERROR: Ошибка вызова Gemini API: {e}")
        return "Произошла ошибка при попытке связаться с AI."

async def find_faq_response(message_text: str) -> str | None:
    if SessionLocal is None: return None
    db = SessionLocal()
    try:
        message_words = set(message_text.lower().split())
        active_faqs = db.query(FAQ).filter(FAQ.enabled == True).all()
        for faq in active_faqs:
            faq_keywords = set(faq.keywords.lower().split(','))
            if any(word in message_words for word in faq_keywords):
                return faq.response_text
    except OperationalError:
        print("ERROR: Ошибка БД при поиске FAQ.")
    finally:
        db.close()
    return None


# --- 6. HANDLERS (ЛОГИКА) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if SessionLocal is None:
        await update.message.reply_text("⚠️ Ошибка: База данных не инициализирована. Проверьте логи сервера.")
        return
    db = SessionLocal()
    try:
        welcome_text = get_db_setting(db, 'welcome_text',
            "🎉 Привет! Бот запущен, БД подключена. Я готов к работе! Используйте /admin для настроек.")
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

async def add_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Эта команда доступна только администратору.")
        return
    if SessionLocal is None:
        await update.message.reply_text("⚠️ Ошибка: База данных не инициализирована.")
        return
    if len(context.args) < 1 or ';' not in " ".join(context.args):
        await update.message.reply_text("❌ Использование: /addfaq <ключи через запятую>; <текст ответа>")
        return
    db = SessionLocal()
    try:
        full_text = " ".join(context.args)
        keywords_part, response_part = full_text.split(';', 1)
        keywords = keywords_part.strip().lower()
        response_text = response_part.strip()
        new_faq = FAQ(keywords=keywords, response_text=response_text, enabled=True)
        db.add(new_faq)
        db.commit()
        await update.message.reply_text(f"✅ Новый FAQ сохранен!\nКлючи: {keywords}")
    except Exception as e:
        db.rollback()
        await update.message.reply_text(f"❌ Ошибка сохранения FAQ: {e}")
    finally:
        db.close()

async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    mention_username = f"@{context.bot.username}"
    clean_text = message_text.replace(mention_username, "", 1).strip()
    if not clean_text:
        await update.message.reply_text("Я здесь. Спрашивайте, я слушаю! 🤖")
        return
    if await analyze_for_scam(clean_text):
        await update.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Вопрос удален AI. Причина: Мошенничество/спам.")
        return
    await update.message.reply_text("Думаю... (использую Gemini)")
    response = await generate_response(clean_text)
    await update.message.reply_text(response)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text

    # 1. МОДУЛЬ GEMINI (Глубокая проверка)
    if await analyze_for_scam(message_text):
        await update.message.delete()
        warning = "❌ Сообщение удалено AI (Gemini). Причина: Обнаружено мошенничество/спам."
        await context.bot.send_message(chat_id=update.effective_chat.id, text=warning)
        return

    # 2. --- ИЗМЕНЕНИЕ: МОДУЛЬ ZERO-SHOT через API ---
    mod_threshold = MOD_THRESHOLD_DEFAULT
    if SessionLocal:
        db = SessionLocal()
        try:
            mod_threshold = float(get_db_setting(db, 'mod_threshold', str(MOD_THRESHOLD_DEFAULT)))
        finally:
            db.close()

    candidate_labels = ["токсичность", "предложение работы", "реклама", "финансовый спам"]
    results = await classify_text_huggingface(message_text, candidate_labels)

    if results and results.get('labels') and results.get('scores'):
        best_label = results['labels'][0]
        best_score = results['scores'][0]

        if (best_label in ["токсичность", "реклама"]) and best_score > mod_threshold:
            await update.message.delete()
            warning = f"❌ Сообщение удалено AI. Причина: {best_label} (Уверенность: {best_score:.2%})."
            await context.bot.send_message(chat_id=update.effective_chat.id, text=warning)
            return

    # 3. ТРИГГЕРНЫЙ ПОИСК (FAQ)
    faq_answer = await find_faq_response(message_text)
    if faq_answer:
        await update.message.reply_text(faq_answer)
        return

    # 4. Базовый ответ (если все проверки пройдены)
    # await update.message.reply_text(f"Я получил твое сообщение: '{message_text}'") # Можно отключить для тишины


# --- АДМИН-МЕНЮ ХЕНДЛЕРЫ (без изменений) ---

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Доступ запрещен.")
        return
    context.user_data['state'] = None
    keyboard = [
        [InlineKeyboardButton("📚 Управление FAQ (Через /addfaq)", callback_data='admin_info')],
        [InlineKeyboardButton("⚙️ Настройка порога модерации", callback_data='admin_moderation')],
        [InlineKeyboardButton("📝 Сбросить состояние", callback_data='admin_reset_state')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Выберите опцию для настройки гибридности:', reply_markup=reply_markup)

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'admin_moderation':
        await manage_moderation_menu(query.message, context)
    elif data == 'admin_reset_state':
        context.user_data['state'] = None
        await query.message.edit_text("✅ Состояние сброшено. Введите /admin снова для меню.")
    elif data == 'admin_info':
        await query.message.edit_text("Для добавления FAQ используйте команду `/addfaq ключи; ответ`", parse_mode='Markdown')

async def manage_moderation_menu(message, context: ContextTypes.DEFAULT_TYPE):
    if SessionLocal is None:
        await message.edit_text("⚠️ Ошибка: База данных не инициализирована.")
        return
    db = SessionLocal()
    try:
        current_threshold = get_db_setting(db, 'mod_threshold', str(MOD_THRESHOLD_DEFAULT))
    finally:
        db.close()
    text = f"Текущий порог Zero-Shot (отсечение): **{current_threshold}**\n\nВведите новое значение (0.00 до 1.00):"
    context.user_data['state'] = STATE_AWAITING_NEW_THRESHOLD
    await message.edit_text(text, parse_mode='Markdown')

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == STATE_AWAITING_NEW_THRESHOLD:
        new_value = update.message.text
        try:
            float_value = float(new_value)
            if not (0.0 <= float_value <= 1.0): raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Ошибка: Введите число от 0.00 до 1.00.")
            return
        db = SessionLocal()
        try:
            set_db_setting(db, 'mod_threshold', str(float_value))
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Порог модерации обновлен до: **{float_value}**.")
        except Exception as e:
            db.rollback()
            await update.message.reply_text(f"❌ Ошибка БД при сохранении: {e}")
        finally:
            db.close()
    else:
        await update.message.reply_text("Я не знаю, что делать с этим текстом. Введите /admin для меню.")


# --- 7. REGISTER HANDLERS ---
if application:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addfaq", add_faq))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern='^admin_'))
    application.add_handler(MessageHandler(
        filters.TEXT & filters.User(user_id=int(ADMIN_CHAT_ID)) & ~filters.COMMAND, handle_admin_input
    ))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity("mention"), handle_mention))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
else:
    print("ERROR: Application не инициализирован, хэндлеры не добавлены.")


# --- 8. WEB SERVER ENDPOINTS ---
@app.post("/api/webhook")
async def webhook(request: Request):
    if not application or not bot:
        return {"status": "error", "message": "Bot not initialized"}
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.initialize()
        await application.process_update(update)
        await application.shutdown()
        return {"status": "ok"}
    except Exception as e:
        print(f"ERROR: Ошибка в обработчике вебхука: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/")
def health_check():
    return {"status": "Bot is alive and ready for Vercel!"}
