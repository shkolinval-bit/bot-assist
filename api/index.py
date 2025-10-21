# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v15.2 - с самодиагностикой AI)

import os
import asyncio
import httpx
from fastapi import FastAPI, Request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from typing import Optional
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base

# --- 1. CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("POSTGRES_PRISMA_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
HUGGING_FACE_TOKEN = os.getenv("HUGGING_FACE_TOKEN")
HUGGING_FACE_MODEL_URL = "https://api-inference.huggingface.co/models/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-fein-tuned"

# Константы
MOD_THRESHOLD_DEFAULT = 0.85
STATE_AWAITING_NEW_THRESHOLD = 'AWAITING_NEW_THRESHOLD'

# --- 2. DATABASE SETUP (СИНХРОННОЕ) ---
Base = declarative_base()
engine = None
SessionLocal = None

try:
    if not DATABASE_URL:
        raise ValueError("Переменная окружения POSTGRES_PRISMA_URL не найдена!")
    main_db_url = DATABASE_URL.split('?')[0]
    db_url_adapted = main_db_url.replace("postgres://", "postgresql+pg8000://", 1)
    engine = create_engine(db_url_adapted)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine, checkfirst=True)
    print("INFO: Синхронный Engine и SessionLocal успешно созданы.")
except Exception as e:
    print(f"FATAL ERROR during engine setup: {e}")

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

# --- ИНИЦИАЛИЗАЦИЯ FastAPI ---
app = FastAPI()

# --- СИНХРОННЫЕ ФУНКЦИИ-ПОМОЩНИКИ ДЛЯ БД ---
def get_db_setting(session, key: str, default: str) -> str:
    setting = session.query(Settings).filter_by(setting_key=key).first()
    return setting.setting_value if setting else default

def set_db_setting(session, key: str, value: str):
    setting = session.query(Settings).filter_by(setting_key=key).first()
    if setting:
        setting.setting_value = value
    else:
        session.add(Settings(setting_key=key, setting_value=value))
    session.commit()

def find_faq_response(session, message_text: str) -> str | None:
    message_words = set(message_text.lower().split())
    active_faqs = session.query(FAQ).filter(FAQ.enabled == True).all()
    for faq in active_faqs:
        faq_keywords = set(faq.keywords.lower().split(','))
        if any(word in message_words for word in faq_keywords):
            return faq.response_text
    return None

def add_faq_db(session, keywords: str, response_text: str):
    new_faq = FAQ(keywords=keywords, response_text=response_text, enabled=True)
    session.add(new_faq)
    session.commit()

# --- АСИНХРОННЫЕ ФУНКЦИИ-ПОМОЩНИКИ ДЛЯ AI ---
async def classify_text_huggingface(text: str, labels: list) -> Optional[dict]:
    if not HUGGING_FACE_TOKEN:
        print("LOG_AI: HUGGING_FACE_TOKEN не найден.")
        return None
    headers = {"Authorization": f"Bearer {HUGGING_FACE_TOKEN}"}
    payload = {"inputs": text, "parameters": {"candidate_labels": labels, "multi_label": True}}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(HUGGING_FACE_MODEL_URL, headers=headers, json=payload, timeout=10.0)
            return response.json() if response.status_code == 200 else {"error": response.text}
        except httpx.RequestError as e:
            print(f"LOG_AI_ERROR: Ошибка запроса к Hugging Face: {e}")
            return None

async def analyze_for_scam(text_to_analyze: str) -> bool:
    if not GEMINI_API_KEY:
        print("LOG_AI: GEMINI_API_KEY не найден.")
        return False
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        prompt = (f"Проанализируй на финансовый спам, мошенничество или фишинг. Ответь только ДА или НЕТ.\n\nСообщение: \"{text_to_analyze}\"")
        response = await model.generate_content_async(prompt)
        return response.text.strip().upper() == 'ДА'
    except Exception as e:
        print(f"LOG_AI_ERROR: Ошибка вызова Gemini API (скам): {e}")
        return False

async def generate_response(user_prompt: str) -> str:
    if not GEMINI_API_KEY:
        print("LOG_AI: GEMINI_API_KEY не найден.")
        return "Ключ Gemini API не настроен."
    try:
        from google import genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
        response = await model.generate_content_async(user_prompt)
        return response.text
    except Exception as e:
        print(f"LOG_AI_ERROR: Ошибка вызова Gemini API (генерация): {e}")
        return f"Произошла ошибка при обращении к ИИ: {e}"

# --- HANDLERS (ГИБРИДНЫЕ) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if SessionLocal is None:
        await update.message.reply_text("⚠️ Критическая ошибка конфигурации. SessionLocal не создан.")
        return
    session = SessionLocal()
    try:
        welcome_text = await asyncio.to_thread(get_db_setting, session, 'welcome_text', "🎉 Бот запущен!")
        await update.message.reply_text(welcome_text)
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при работе с БД: {e}")
    finally:
        await asyncio.to_thread(session.close)

async def add_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Эта команда доступна только администратору.")
        return
    if SessionLocal is None:
        await update.message.reply_text("⚠️ База данных не инициализирована.")
        return
    if len(context.args) < 1 or ';' not in " ".join(context.args):
        await update.message.reply_text("❌ Использование: /addfaq <ключи,через,запятую>; <текст ответа>")
        return
    
    session = SessionLocal()
    try:
        full_text = " ".join(context.args)
        keywords_part, response_part = full_text.split(';', 1)
        await asyncio.to_thread(add_faq_db, session, keywords_part.strip().lower(), response_part.strip())
        await update.message.reply_text(f"✅ Новый FAQ сохранен!\nКлючи: {keywords_part.strip().lower()}")
    except Exception as e:
        await asyncio.to_thread(session.rollback)
        await update.message.reply_text(f"❌ Ошибка сохранения FAQ: {e}")
    finally:
        await asyncio.to_thread(session.close)

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
    session = SessionLocal()
    try:
        threshold_str = await asyncio.to_thread(get_db_setting, session, 'mod_threshold', str(MOD_THRESHOLD_DEFAULT))
        mod_threshold = float(threshold_str)
    except (ValueError, TypeError):
        pass
    finally:
        await asyncio.to_thread(session.close)

    candidate_labels = ["токсичность", "предложение работы", "реклама", "финансовый спам"]
    results = await classify_text_huggingface(message_text, candidate_labels)
    if results and results.get('labels') and results.get('scores'):
        best_label, best_score = results['labels'][0], results['scores'][0]
        if (best_label in ["токсичность", "реклама"]) and best_score > mod_threshold:
            await update.message.delete()
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Сообщение удалено ИИ. Причина: {best_label} (Уверенность: {best_score:.2%}).")
            return

    session = SessionLocal()
    try:
        faq_answer = await asyncio.to_thread(find_faq_response, session, message_text)
        if faq_answer:
            await update.message.reply_text(faq_answer)
            return
    finally:
        await asyncio.to_thread(session.close)

async def run_ai_self_test(query: CallbackQuery):
    await query.edit_message_text("🔍 Провожу самодиагностику нейросетей...")
    report = ["**Отчет по самодиагностике:**\n"]
    
    report.append("--- **Google Gemini** ---")
    if os.getenv("GEMINI_API_KEY"):
        report.append("✅ Ключ `GEMINI_API_KEY`: **Найден**")
        try:
            test_response = await generate_response("Ты работаешь? Ответь только одним словом: Да")
            if "да" in test_response.lower():
                report.append("✅ Тест API: **Успешно**")
            else:
                report.append(f"❌ Тест API: **Неожиданный ответ** (получено: '{test_response[:100]}...')")
        except Exception as e:
            report.append(f"❌ Тест API: **Провален с ошибкой** ({e})")
    else:
        report.append("❌ Ключ `GEMINI_API_KEY`: **НЕ НАЙДЕН** в настройках Vercel.")

    report.append("\n--- **Hugging Face** ---")
    if os.getenv("HUGGING_FACE_TOKEN"):
        report.append("✅ Ключ `HUGGING_FACE_TOKEN`: **Найден**")
        try:
            hf_response = await classify_text_huggingface("это просто текст для теста", ["тест"])
            if isinstance(hf_response, dict) and 'labels' in hf_response:
                report.append("✅ Тест API: **Успешно**")
            else:
                report.append(f"❌ Тест API: **Неожиданный ответ** (`{hf_response}`)")
        except Exception as e:
             report.append(f"❌ Тест API: **Провален с ошибкой** ({e})")
    else:
        report.append("❌ Ключ `HUGGING_FACE_TOKEN`: **НЕ НАЙДЕН** в настройках Vercel.")
        
    await query.edit_message_text("\n".join(report), parse_mode='Markdown')

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Доступ запрещен.")
        return
    context.user_data['state'] = None
    keyboard = [
        [InlineKeyboardButton("⚙️ Настроить порог модерации", callback_data='admin_moderation')],
        [InlineKeyboardButton("🧠 Проверить нейросети", callback_data='admin_test_ai')]
    ]
    await update.message.reply_text('Меню администратора:', reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'admin_moderation':
        if SessionLocal is None:
            await query.message.edit_text("⚠️ База данных не инициализирована.")
            return
        session = SessionLocal()
        try:
            current_threshold = await asyncio.to_thread(get_db_setting, session, 'mod_threshold', str(MOD_THRESHOLD_DEFAULT))
            text = f"Текущий порог модерации: **{current_threshold}**\n\nВведите новое значение (от 0.00 до 1.00):"
            context.user_data['state'] = STATE_AWAITING_NEW_THRESHOLD
            await query.message.edit_text(text, parse_mode='Markdown')
        except Exception as e:
            await query.message.edit_text(f"Ошибка получения настроек: {e}")
        finally:
            await asyncio.to_thread(session.close)

    elif query.data == 'admin_test_ai':
        await run_ai_self_test(query)

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == STATE_AWAITING_NEW_THRESHOLD:
        try:
            float_value = float(update.message.text)
            if not (0.0 <= float_value <= 1.0): raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Ошибка: Введите число от 0.00 до 1.00.")
            return
        
        session = SessionLocal()
        try:
            await asyncio.to_thread(set_db_setting, session, 'mod_threshold', str(float_value))
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Порог модерации обновлен до: **{float_value}**.", parse_mode='Markdown')
        except Exception as e:
            await asyncio.to_thread(session.rollback)
            await update.message.reply_text(f"❌ Ошибка БД при сохранении: {e}")
        finally:
            await asyncio.to_thread(session.close)

# --- WEB SERVER ENDPOINTS ---
@app.post("/api/webhook")
async def webhook(request: Request):
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addfaq", add_faq))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern='^admin_'))
    application.add_handler(MessageHandler(filters.TEXT & filters.User(user_id=int(ADMIN_CHAT_ID)) & ~filters.COMMAND, handle_admin_input))
    application.add_handler(MessageHandler(filters.TEXT & filters.Entity("mention"), handle_mention))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
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
    return {"status": "Бот жив. Версия v15.2 (с самодиагностикой AI)."}
