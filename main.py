#!/usr/bin/env python3
# coding: utf-8

import os
import time
import logging
import sqlite3
import datetime
from threading import Thread

from flask import Flask
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ----------------------------
# Настройка логирования
# ----------------------------
LOGFILE = os.environ.get("BOT_LOGFILE", "bot.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGFILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ----------------------------
# Ограничение времени между сообщениями (5 секунд)
# ----------------------------
user_last_message_time = {}
MESSAGE_COOLDOWN = 5  # секунд

# Ограничение для кнопки (30 секунд)
button_cooldown_users = {}
BUTTON_COOLDOWN = 30  # секунд

def check_cooldown(user_id):
    """Проверяет кулдаун и возвращает оставшееся время"""
    current_time = time.time()
    last_time = user_last_message_time.get(user_id, 0)
    
    time_passed = current_time - last_time
    if time_passed < MESSAGE_COOLDOWN:
        return MESSAGE_COOLDOWN - time_passed
    
    user_last_message_time[user_id] = current_time
    return 0

def check_button_cooldown(user_id):
    """Проверяет кулдаун для кнопки и возвращает оставшееся время"""
    current_time = time.time()
    last_time = button_cooldown_users.get(user_id, 0)
    
    time_passed = current_time - last_time
    if time_passed < BUTTON_COOLDOWN:
        return BUTTON_COOLDOWN - time_passed
    
    button_cooldown_users[user_id] = current_time
    return 0

def restore_button(user_id):
    """Восстанавливает кнопку через 30 секунд"""
    time.sleep(BUTTON_COOLDOWN)
    try:
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(KeyboardButton("📞 Попросить связаться со мной."))
        bot.send_message(user_id, "✅ Кнопка запроса связи снова доступна!", reply_markup=markup)
    except Exception as e:
        logger.error(f"Failed to restore button for user {user_id}: {e}")

# ----------------------------
# Flask keep-alive
# ----------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Bot is alive and running!"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    # В Render обычно WSGI контейнер, но для keep-alive  встроенный сервер в отдельном потоке
    try:
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
    except Exception as e:
        logger.exception("Flask failed: %s", e)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()
    logger.info("Flask keep-alive thread started.")

# Бот и база данных

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment. Please set BOT_TOKEN.")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8401905691"))

bot = telebot.TeleBot(BOT_TOKEN) if BOT_TOKEN else None

DB_PATH = os.environ.get("DB_PATH", "users.db")

def init_db():
    """Создаёт таблицы, если их нет."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT)''')
        conn.commit()
        conn.close()
        logger.info("Database initialized at %s", DB_PATH)
    except Exception:
        logger.exception("Failed to initialize DB")

def register_user(user_id, username, first_name, last_name):
    """Сохраняет/обновляет пользователя в БД."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?)",
                  (user_id, username, first_name, last_name))
        conn.commit()
        conn.close()
        logger.debug("Registered user %s (%s)", user_id, username)
    except Exception:
        logger.exception("Failed to register user %s", user_id)

user_reply_mode = {}

# ----------------------------
# Хэндлеры бота
# ----------------------------
if bot:
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        try:
            user_id = int(message.from_user.id)
            register_user(user_id,
                          message.from_user.username,
                          message.from_user.first_name,
                          message.from_user.last_name)

            welcome_text = (
                "Привет. Я бот-пересыльщик сообщений для kvazador.\n\n"
                "Для связи с kvazador сначала вам необходимо отправить сообщение (сколько потребуется) здесь. "
                "Ответ может поступить через данного бота, либо вам в ЛС.\n\n"
                "Ваше сообщение будет доставлено ему от вашего имени.\n\n"
                "Сам kvazador свяжется с вами как только заметит ваше сообщение в боте. Просто представьте что это чат с ним, а не какой-то чат с ботом-пересыльщиком сообщений."
            )

            markup = ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(KeyboardButton("📞 Попросить связаться со мной."))
            bot.send_message(user_id, welcome_text, reply_markup=markup)
        except Exception:
            logger.exception("Error in /start handler for message: %s", message)

    @bot.message_handler(func=lambda message: message.text == "📞 Попросить связаться со мной.")
    def handle_contact_request(message):
        try:
            user_id = int(message.from_user.id)
            
            # Проверка кулдауна для кнопки
            cooldown_remaining = check_button_cooldown(user_id)
            if cooldown_remaining > 0:
                bot.send_message(
                    user_id, 
                    f"⏳ Кнопка будет доступна через {int(cooldown_remaining)} секунд",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            # Убираем кнопку на 30 секунд
            bot.send_message(
                user_id, 
                "✅ Ваш запрос на связь отправлен. Ожидайте ответа.\n\n"
                f"🕒 Кнопка снова появится через {BUTTON_COOLDOWN} секунд",
                reply_markup=ReplyKeyboardRemove()
            )
            
            # Отправляем уведомление админу
            bot.send_message(
                ADMIN_ID,
                f"📞 Пользователь {message.from_user.first_name} "
                f"@{message.from_user.username or 'без username'} "
                f"(ID: {user_id}) просит связаться."
            )
            
            # Запускаем восстановление кнопки через 30 секунд
            Thread(target=restore_button, args=(user_id,), daemon=True).start()
            
        except Exception:
            logger.exception("Error in contact request handler: %s", message)

    @bot.message_handler(commands=['reply'])
    def start_reply_mode(message):
        try:
            user_id = int(message.from_user.id)
            if user_id != ADMIN_ID:
                bot.send_message(user_id, "❌ Эта команда только для администратора.")
                return

            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(ADMIN_ID, "❌ Используй: /reply user_id\nПример: /reply 123456789")
                return

            try:
                target_id = int(parts[1])
            except ValueError:
                bot.send_message(ADMIN_ID, "❌ Неверный user_id. Это должно быть целое число.")
                return

            user_reply_mode[ADMIN_ID] = target_id
            bot.send_message(ADMIN_ID, f"🔹 Режим ответа включен для пользователя ID: {target_id}")
        except Exception:
            logger.exception("Error in /reply handler: %s", message)

    @bot.message_handler(commands=['stop'])
    def stop_reply_mode(message):
        try:
            user_id = int(message.from_user.id)
            if user_id == ADMIN_ID:
                if ADMIN_ID in user_reply_mode:
                    del user_reply_mode[ADMIN_ID]
                    bot.send_message(ADMIN_ID, "🔹 Режим ответа выключен.")
                else:
                    bot.send_message(ADMIN_ID, "🔹 Режим ответа не был включен.")
        except Exception:
            logger.exception("Error in /stop handler: %s", message)

    @bot.message_handler(func=lambda message: int(message.from_user.id) == ADMIN_ID and ADMIN_ID in user_reply_mode)
    def handle_admin_reply(message):
        try:
            if message.content_type != 'text':
                bot.send_message(ADMIN_ID, "❌ В режиме ответа можно отправлять только текст.")
                return

            target_user_id = user_reply_mode.get(ADMIN_ID)
            if not target_user_id:
                bot.send_message(ADMIN_ID, "❌ Целевой пользователь не найден.")
                return

            try:
                bot.send_message(target_user_id, f"💌 Поступил ответ от kvazador:\n\n{message.text}")
                bot.send_message(ADMIN_ID, f"✅ Ответ отправлен пользователю ID: {target_user_id}")
            except Exception as e:
                logger.exception("Failed to send admin reply to %s: %s", target_user_id, e)
                bot.send_message(ADMIN_ID, f"❌ Ошибка отправки: {e}")
        except Exception:
            logger.exception("Error in admin reply handler: %s", message)

    @bot.message_handler(content_types=['text'])
    def forward_text_message(message):
        try:
            user_id = int(message.from_user.id)

            # Игнорим команды (начинающиеся с /)
            if message.text.startswith('/'):
                return

            # Специальная клавиша уже обрабатывается отдельно
            if message.text == "📞 Попросить связаться со мной.":
                return handle_contact_request(message)

            # Проверка кулдауна (кроме админа)
            if user_id != ADMIN_ID:
                cooldown_remaining = check_cooldown(user_id)
                if cooldown_remaining > 0:
                    bot.send_message(
                        user_id, 
                        f"⏳ Пожалуйста, подождите {int(cooldown_remaining)} секунд перед отправкой следующего сообщения."
                    )
                    return

            if user_id == ADMIN_ID and ADMIN_ID not in user_reply_mode:
                bot.send_message(ADMIN_ID, "ℹ️ Чтобы ответить пользователю, используй команду /reply user_id")
                return

            user_info = f"👤 От: {message.from_user.first_name}"
            if message.from_user.last_name:
                user_info += f" {message.from_user.last_name}"
            if message.from_user.username:
                user_info += f" (@{message.from_user.username})"
            user_info += f"\n🆔 ID: {user_id}"
            user_info += f"\n⏰ {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}"

            bot.send_message(ADMIN_ID, f"{user_info}\n\n📨 Сообщение:\n\n{message.text}")
            bot.send_message(user_id, "✅ Сообщение отправлено kvazador!")
        except Exception as e:
            logger.exception("Failed to forward text message from %s: %s", getattr(message, "from_user", None), e)
            try:
                bot.send_message(user_id, "❌ Ошибка отправки. Пользователь kvazador не найден.")
            except Exception:
                logger.exception("Also failed to notify user about forwarding error.")

    @bot.message_handler(content_types=['photo', 'voice', 'video', 'document', 'audio'])
    def forward_media_message(message):
        try:
            user_id = int(message.from_user.id)

            # Проверка кулдауна (кроме админа)
            if user_id != ADMIN_ID:
                cooldown_remaining = check_cooldown(user_id)
                if cooldown_remaining > 0:
                    bot.send_message(
                        user_id, 
                        f"⏳ Пожалуйста, подождите {int(cooldown_remaining)} секунд перед отправкой следующего сообщения."
                    )
                    return

            user_info = f"👤 От: {message.from_user.first_name}"
            if message.from_user.last_name:
                user_info += f" {message.from_user.last_name}"
            if message.from_user.username:
                user_info += f" (@{message.from_user.username})"
            user_info += f"\n🆔 ID: {user_id}"
            user_info += f"\n⏰ {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}"

            caption = f"{user_info}\n\n"
            if message.caption:
                caption += f"📝 Подпись: {message.caption}"

            # Отправка соответствующего типа медиа админу
            if message.photo:
                bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=caption)
            elif message.voice:
                bot.send_voice(ADMIN_ID, message.voice.file_id, caption=caption)
            elif message.video:
                bot.send_video(ADMIN_ID, message.video.file_id, caption=caption)
            elif message.document:
                bot.send_document(ADMIN_ID, message.document.file_id, caption=caption)
            elif message.audio:
                bot.send_audio(ADMIN_ID, message.audio.file_id, caption=caption)
            else:
                # На всякий случай — если тип не покрыт
                bot.send_message(ADMIN_ID, f"{user_info}\n📨 Прислал медиа, но тип не определён.")

            bot.send_message(user_id, "✅ Медиа-сообщение отправлено kvazador!")
        except Exception as e:
            logger.exception("Ошибка отправки медиа: %s", e)
            try:
                bot.send_message(user_id, "❌ Ошибка отправки медиа.")
            except Exception:
                logger.exception("Failed to notify user about media send error.")

    @bot.message_handler(content_types=['contact', 'location'])
    def forward_contact_location(message):
        try:
            user_id = int(message.from_user.id)

            # Проверка кулдауна (кроме админа)
            if user_id != ADMIN_ID:
                cooldown_remaining = check_cooldown(user_id)
                if cooldown_remaining > 0:
                    bot.send_message(
                        user_id, 
                        f"⏳ Пожалуйста, подождите {int(cooldown_remaining)} секунд перед отправкой следующего сообщения."
                    )
                    return

            user_info = f"👤 От: {message.from_user.first_name}"
            if message.from_user.username:
                user_info += f" (@{message.from_user.username})"
            user_info += f"\n🆔 ID: {user_id}"
            user_info += f"\n⏰ {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}"

            if message.contact:
                # Отправляем контакт админу
                bot.send_contact(
                    ADMIN_ID,
                    phone_number=message.contact.phone_number,
                    first_name=message.contact.first_name,
                    last_name=getattr(message.contact, "last_name", None)
                )
                bot.send_message(ADMIN_ID, f"{user_info}\n📞 Прислал контакт")
            elif message.location:
                bot.send_location(
                    ADMIN_ID,
                    message.location.latitude,
                    message.location.longitude,
                )
                bot.send_message(ADMIN_ID, f"{user_info}\n📍 Прислал локацию")
            else:
                bot.send_message(ADMIN_ID, f"{user_info}\n📨 Прислал контакт/локацию, но детали отсутствуют.")

            bot.send_message(user_id, "✅ Данные отправлены kvazador!")
        except Exception as e:
            logger.exception("Ошибка отправки контакта/локации: %s", e)
            try:
                bot.send_message(user_id, "❌ Ошибка отправки.")
            except Exception:
                logger.exception("Failed to notify user about contact/location send error.")

# ----------------------------
# Основной цикл запуска бота
# ----------------------------

def start_bot_loop():
    """Запускает бота и перезапускает при ошибках (без рекурсии)."""
    if not bot:
        logger.error("Bot object is not created because BOT_TOKEN is missing.")
        return

    init_db()

    # Проверка токена и получение информации о боте
    try:
        logger.info("Attempting bot.get_me() to verify token...")
        me = bot.get_me()
        logger.info("Bot connected as: %s (id=%s)", me.username, me.id)
    except Exception as e:
        logger.exception("Failed to connect to Telegram. Check BOT_TOKEN. %s", e)
        return

    logger.info("Bot is ready to receive messages.")

    # Постоянный цикл с перезапуском polling при исключениях
    while True:
        try:
            # logger_level должен быть числом из модуля logging, а не строкой
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60,
                logger_level=logging.INFO
            )
        except Exception as e:
            logger.exception("Polling error: %s", e)
            logger.info("Restarting polling in 10 seconds...")
            time.sleep(10)

if __name__ == "__main__":
    keep_alive()
    try:
        start_bot_loop()
    except KeyboardInterrupt:
        logger.info("Bot stopped by KeyboardInterrupt")
    except Exception:
        logger.exception("Fatal error in main")
