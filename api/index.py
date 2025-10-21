# Файл: api/index.py (ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ v20+ с Админ-меню)

import os
from fastapi import FastAPI, Request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
# !!! ИСПРАВЛЕНИЕ: Используем Application и ApplicationBuilder для v20+ !!!
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, 
    MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
# !!! НОВЫЙ ИМПОРТ: Для работы с командами администратора !!!
from typing import Optional 
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError

# --- 1. CONFIGURATION (READ FROM RENDER ENVIRONMENT) ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Константы для логики и состояний
MOD_THRESHOLD_DEFAULT = 0.85 
STATE_AWAITING_NEW_THRESHOLD = 'AWAITING_NEW_THRESHOLD'

# --- 2. ИНИЦИАЛИЗАЦИЯ НЕЙРОСЕТИ (ГИБРИДНАЯ СИСТЕМА) ---
text_classifier = None
try:
    from transformers import pipeline
    # !!! ФИКС: Используем актуальную модель для Zero-Shot !!!
    text_classifier = pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-fein-tuned"
    )
    print("INFO: Zero-Shot классификатор загружен.")
except Exception as e:
    print(f"ERROR: Ошибка загрузки Zero-Shot: {e}. Проверьте имя модели и библиотеку.")
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

SessionLocal = None
try:
    # !!! ОЖИДАЕТСЯ, ЧТО psycopg2-binary ДОБАВЛЕН В requirements.txt !!!
    engine = create_engine(DATABASE_URL) 
    Base.metadata.create_all(bind=engine) # Создаем таблицы при первом запуске
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    print("INFO: База данных настроена и таблицы созданы/проверены.")
except Exception as e:
    # Ошибка импорта psycopg2 или подключения к БД приведет к SessionLocal = None
    print(f"ERROR: Ошибка настройки базы данных: {e}. SessionLocal не инициализирован.")
    SessionLocal = None


# --- 4. BOT & APP INITIALIZATION (ФИКС v20+) ---
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
    """Извлекает настройку из БД или возвращает значение по умолчанию."""
    setting = db_session.query(Settings).filter_by(setting_key=key).first()
    return setting.setting_value if setting else default

def set_db_setting(db_session, key: str, value: str):
    """Устанавливает или обновляет настройку в БД."""
    setting = db_session.query(Settings).filter_by(setting_key=key).first()
    if setting:
        setting.setting_value = value
    else:
        db_session.add(Settings(setting_key=key, setting_value=value))
    db_session.commit()

async def analyze_for_scam(text_to_analyze: str) -> bool:
    """Отправляет текст в Gemini API для глубокого анализа мошенничества."""
    if not GEMINI_API_KEY:
        return False
        
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = (
            "Ты — строгий модератор. Проанализируй сообщение. "
            "Если оно является явным финансовым спамом, мошенническим предложением о работе, "
            "или фишингом, ответь ТОЛЬКО одним словом: ДА. В противном случае ответь ТОЛЬКО одним словом: НЕТ.\n\n"
            f"Сообщение: \"{text_to_analyze}\""
        )
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text.strip().upper() == 'ДА'
        
    except Exception as e:
        print(f"ERROR: Ошибка вызова Gemini API: {e}")
        return False

# !!! НОВАЯ ФУНКЦИЯ !!!
async def generate_response(user_prompt: str) -> str:
    """Генерирует развернутый ответ от Gemini."""
    if not GEMINI_API_KEY:
        return "Извините, Gemini API-ключ не настроен."
        
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_prompt
        )
        return response.text
        
    except Exception as e:
        print(f"ERROR: Ошибка вызова Gemini API: {e}")
        return "Произошла ошибка при попытке связаться с AI."

# !!! НОВАЯ ФУНКЦИЯ !!!
async def find_faq_response(message_text: str) -> str | None:
    """Ищет совпадение ключевых слов в таблице FAQ (Триггеры)."""
    if SessionLocal is None:
        return None

    db = SessionLocal()
    try:
        message_words = set(message_text.lower().split())
        active_faqs = db.query(FAQ).filter(FAQ.enabled == True).all()

        for faq in active_faqs:
            # Ищем совпадение по любому слову
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
    """Обработчик /start."""
    # !!! ФИКС: Проверка инициализации БД !!!
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
        # Уведомление админа
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"Бот успешно запущен. Пользователь {update.effective_user.name} ввел команду /start."
        )
    except Exception as e:
        print(f"ОШИБКА УВЕДОМЛЕНИЯ АДМИНА: {e}")

# !!! НОВЫЙ ХЕНДЛЕР: Добавление FAQ (Триггеров) !!!
async def add_faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет новый вопрос-ответ в БД, доступно только админу."""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Эта команда доступна только администратору.")
        return
    if SessionLocal is None:
        await update.message.reply_text("⚠️ Ошибка: База данных не инициализирована.")
        return

    # Формат команды: /addfaq ключ1,ключ2; Ваш ответ
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

# !!! НОВЫЙ ХЕНДЛЕР: Ответ на упоминание @bot !!!
async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отвечает, когда бота упоминают (@bot) в групповом чате."""
    message_text = update.message.text
    
    # Пытаемся удалить юзернейм бота из текста
    mention_username = f"@{context.bot.username}"
    clean_text = message_text.replace(mention_username, "", 1).strip()
    
    if not clean_text:
        await update.message.reply_text("Я здесь. Спрашивайте, я слушаю! 🤖")
        return
        
    # Модерация: Пропускаем через Gemini, чтобы избежать спама/мошенничества даже при вызове
    if await analyze_for_scam(clean_text):
        await update.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Вопрос удален AI. Причина: Мошенничество/спам.")
        return
        
    # Ответ через Gemini
    await update.message.reply_text("Думаю... (использую Gemini)")
    response = await generate_response(clean_text)
    await update.message.reply_text(response)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основной обработчик: 1. Модерация, 2. Триггер FAQ, 3. Базовый ответ."""
    message_text = update.message.text
    
    # 1. МОДУЛЬ GEMINI (Глубокая проверка)
    if await analyze_for_scam(message_text):
        await update.message.delete()
        warning = "❌ Сообщение удалено AI (Gemini). Причина: Обнаружено мошенничество/спам."
        await context.bot.send_message(chat_id=update.effective_chat.id, text=warning)
        return
        
    # 2. МОДУЛЬ ZERO-SHOT (Базовая проверка на токсичность/рекламу)
    if text_classifier:
        mod_threshold = MOD_THRESHOLD_DEFAULT
        if SessionLocal:
            db = SessionLocal()
            try:
                mod_threshold = float(get_db_setting(db, 'mod_threshold', str(MOD_THRESHOLD_DEFAULT)))
            except (ValueError, TypeError):
                pass
            finally:
                db.close()
        
        candidate_labels = ["токсичность", "предложение работы", "реклама", "финансовый спам"]
        results = text_classifier(message_text, candidate_labels, multi_label=True)
        best_label = results['labels'][0]
        best_score = results['scores'][0]
        
        if (best_label in ["токсичность", "реклама"]) and best_score > mod_threshold:
            await update.message.delete()
            warning = f"❌ Сообщение удалено AI. Причина: {best_label} (Уверенность: {best_score:.2%})."
            await context.bot.send_message(chat_id=update.effective_chat.id, text=warning)
            return

    # 3. ТРИГГЕРНЫЙ ПОИСК (FAQ) - Имеет приоритет над базовым ответом
    faq_answer = await find_faq_response(message_text)
    if faq_answer:
        await update.message.reply_text(faq_answer)
        return
        
    # 4. Базовый ответ (если все проверки пройдены)
    await update.message.reply_text(f"Я получил твое сообщение: '{message_text}'")


# !!! АДМИН-МЕНЮ ХЕНДЛЕРЫ !!!

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выводит главное меню для администратора."""
    if str(update.effective_user.id) != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔️ Доступ запрещен.")
        return
    
    # Устанавливаем состояние None перед открытием меню
    context.user_data['state'] = None 

    keyboard = [
        [InlineKeyboardButton("📚 Управление FAQ (Через /addfaq)", callback_data='admin_info')],
        [InlineKeyboardButton("⚙️ Настройка порога модерации", callback_data='admin_moderation')],
        [InlineKeyboardButton("📝 Сбросить состояние", callback_data='admin_reset_state')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text('Выберите опцию для настройки гибридности:', reply_markup=reply_markup)


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия кнопок в админ-меню."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == 'admin_moderation':
        # Переход к настройке порога
        await manage_moderation_menu(query.message, context)
    elif data == 'admin_reset_state':
        context.user_data['state'] = None
        await query.message.edit_text("✅ Состояние сброшено. Введите /admin снова для меню.")
    elif data == 'admin_info':
        await query.message.edit_text("Для добавления FAQ используйте команду `/addfaq ключи; ответ`", parse_mode='Markdown')


async def manage_moderation_menu(message, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий порог и запрашивает новый."""
    if SessionLocal is None:
        await message.edit_text("⚠️ Ошибка: База данных не инициализирована.")
        return

    db = SessionLocal()
    try:
        current_threshold = get_db_setting(db, 'mod_threshold', str(MOD_THRESHOLD_DEFAULT))
    finally:
        db.close()

    text = f"Текущий порог Zero-Shot (отсечение): **{current_threshold}**\n\nВведите новое значение (0.00 до 1.00):"
    
    # ❗️ Устанавливаем состояние пользователя
    context.user_data['state'] = STATE_AWAITING_NEW_THRESHOLD
    
    # Используем edit_text, чтобы обновить сообщение с клавиатурой
    await message.edit_text(text, parse_mode='Markdown')


async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод администратора в режиме настройки."""
    
    state = context.user_data.get('state')
    
    if state == STATE_AWAITING_NEW_THRESHOLD:
        new_value = update.message.text
        
        # 1. Проверка и валидация
        try:
            float_value = float(new_value)
            if not (0.0 <= float_value <= 1.0):
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Ошибка: Введите число от 0.00 до 1.00.")
            return

        # 2. Сохранение в БД
        db = SessionLocal()
        try:
            set_db_setting(db, 'mod_threshold', str(float_value))
            
            # 3. Сброс состояния
            context.user_data['state'] = None
            await update.message.reply_text(f"✅ Порог модерации обновлен до: **{float_value}**.")
            
        except Exception as e:
            db.rollback()
            await update.message.reply_text(f"❌ Ошибка БД при сохранении: {e}")
        finally:
            db.close()
            
    else:
        # Если админ пишет текст, но не в режиме настройки
        await update.message.reply_text("Я не знаю, что делать с этим текстом. Введите /admin для меню.")


# --- 7. REGISTER HANDLERS ---
if application:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addfaq", add_faq)) 
    application.add_handler(CommandHandler("admin", admin_menu)) # <-- Команда меню
    
    # <-- Хендлер для нажатий кнопок Inline Keyboard. Паттерн ищет callback_data, начинающиеся с 'admin_'
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern='^admin_')) 
    
    # <-- Хендлер для ввода текста в режиме настройки. Срабатывает только на текст админа БЕЗ команд
    application.add_handler(MessageHandler(
        filters.TEXT & filters.User(user_id=int(ADMIN_CHAT_ID)) & ~filters.COMMAND, handle_admin_input
    ))
    
    # <-- Хендлер для упоминания @bot (приоритет выше, чем у общего хендлера)
    application.add_handler(MessageHandler(filters.TEXT & filters.Mention(), handle_mention))
    
    # <-- ОБЩИЙ ХЕНДЛЕР (обрабатывает любой другой текст, если не команда и не упоминание)
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
    if not application or not bot:
        print("ERROR: Вебхук вызван, но Application не создан.")
        return {"status": "error", "message": "Bot not initialized"}

    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        
        # КЛЮЧЕВОЙ ФИКС ДЛЯ RuntimeError: Инициализация при каждом вызове
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
