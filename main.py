#!/usr/bin/env python3
# coding: utf-8
# telebot @kvzdr_bot | version: raw 1.1 | upd reason: added admin system and broadcast
'''
telebot @kvzdr_bot | version: raw 1.2 | upd reason: added admin system, broadcast, and statistics
'''
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
                     (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, is_main_admin BOOLEAN DEFAULT FALSE, date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Добавляем главного админа если его нет
        c.execute("INSERT OR IGNORE INTO admins (user_id, username, first_name, is_main_admin) VALUES (?, ?, ?, ?)",
                  (ADMIN_ID, "main_admin", "Main Admin", True))
        
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
        c.execute("INSERT OR REPLACE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                  (user_id, username, first_name, last_name))
        conn.commit()
        conn.close()
        logger.debug("Registered user %s (%s)", user_id, username)
    except Exception:
        logger.exception("Failed to register user %s", user_id)

def is_admin(user_id):
    """Проверяет, является ли пользователь админом"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        return result is not None
    except Exception:
        logger.exception("Failed to check admin status for %s", user_id)
        return False

def is_main_admin(user_id):
    """Проверяет, является ли пользователь главным админом"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins WHERE user_id = ? AND is_main_admin = TRUE", (user_id,))
        result = c.fetchone()
        conn.close()
        return result is not None
    except Exception:
        logger.exception("Failed to check main admin status for %s", user_id)
        return False

def add_admin(user_id, username, first_name):
    """Добавляет обычного админа"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO admins (user_id, username, first_name) VALUES (?, ?, ?)",
                  (user_id, username, first_name))
        conn.commit()
        conn.close()
        logger.info("Added admin %s (%s)", user_id, username)
        return True
    except Exception:
        logger.exception("Failed to add admin %s", user_id)
        return False

def remove_admin(user_id):
    """Удаляет админа (кроме главного)"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("DELETE FROM admins WHERE user_id = ? AND is_main_admin = FALSE", (user_id,))
        conn.commit()
        conn.close()
        logger.info("Removed admin %s", user_id)
        return True
    except Exception:
        logger.exception("Failed to remove admin %s", user_id)
        return False

def get_all_users():
    """Возвращает список всех пользователей"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT user_id, username, first_name, last_name FROM users")
        users = c.fetchall()
        conn.close()
        return users
    except Exception:
        logger.exception("Failed to get users list")
        return []

def get_user_count():
    """Возвращает количество пользователей"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception:
        logger.exception("Failed to get user count")
        return 0

def get_all_admins():
    """Возвращает список всех админов"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT user_id, username, first_name, is_main_admin FROM admins")
        admins = c.fetchall()
        conn.close()
        return admins
    except Exception:
        logger.exception("Failed to get admins list")
        return []

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
                "Сам kvazador свяжется с вами как только заметит ваше сообщение в боте. Просто представьте что это чат с ним, а не какой-то чат с бот-пересыльщиком сообщений."
            )

            markup = ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(KeyboardButton("📞 Попросить связаться со мной."))
            bot.send_message(user_id, welcome_text, reply_markup=markup)
        except Exception:
            logger.exception("Error in /start handler for message: %s", message)

    # ==================== НОВЫЕ КОМАНДЫ: СИСТЕМА АДМИНИСТРИРОВАНИЯ ====================

    @bot.message_handler(commands=['addadmin'])
    def add_admin_command(message):
        """Добавляет обычного админа (только для главного админа)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(user_id, "❌ Используй: /addadmin user_id\nПример: /addadmin 123456789")
                return

            try:
                target_id = int(parts[1])
            except ValueError:
                bot.send_message(user_id, "❌ Неверный user_id. Это должно быть целое число.")
                return

            # Нельзя добавить самого себя (главный админ уже есть)
            if target_id == user_id:
                bot.send_message(user_id, "❌ Вы уже главный администратор.")
                return

            # Получаем информацию о пользователе
            try:
                target_user = bot.get_chat(target_id)
                username = target_user.username
                first_name = target_user.first_name
            except Exception:
                username = None
                first_name = "Unknown"

            if add_admin(target_id, username, first_name):
                bot.send_message(user_id, f"✅ Пользователь {first_name} (ID: {target_id}) добавлен как администратор.")
                
                # Уведомляем нового админа
                try:
                    bot.send_message(target_id, "🎉 Вы были назначены администратором бота!\n\n"
                                                "Теперь вам доступны команды:\n"
                                                "/stats - статистика пользователей\n"
                                                "/getusers - список всех пользователей\n"
                                                "/sendall - рассылка сообщений")
                except Exception:
                    logger.warning("Could not notify new admin %s", target_id)
            else:
                bot.send_message(user_id, "❌ Ошибка при добавлении администратора.")
                
        except Exception:
            logger.exception("Error in /addadmin handler: %s", message)

    @bot.message_handler(commands=['removeadmin'])
    def remove_admin_command(message):
        """Удаляет админа (только для главного админа)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(user_id, "❌ Используй: /removeadmin user_id\nПример: /removeadmin 123456789")
                return

            try:
                target_id = int(parts[1])
            except ValueError:
                bot.send_message(user_id, "❌ Неверный user_id. Это должно быть целое число.")
                return

            # Нельзя удалить главного админа
            if target_id == user_id:
                bot.send_message(user_id, "❌ Нельзя удалить главного администратора.")
                return

            if remove_admin(target_id):
                bot.send_message(user_id, f"✅ Администратор (ID: {target_id}) удален.")
                
                # Уведомляем бывшего админа
                try:
                    bot.send_message(target_id, "ℹ️ Ваши права администратора были отозваны.")
                except Exception:
                    logger.warning("Could not notify removed admin %s", target_id)
            else:
                bot.send_message(user_id, "❌ Ошибка при удалении администратора или администратор не найден.")
                
        except Exception:
            logger.exception("Error in /removeadmin handler: %s", message)

    @bot.message_handler(commands=['admins'])
    def list_admins_command(message):
        """Показывает список всех админов (только для главного админа)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            admins = get_all_admins()
            if not admins:
                bot.send_message(user_id, "📝 Список администраторов пуст.")
                return

            admin_list = "📋 Список администраторов:\n\n"
            for admin in admins:
                admin_id, username, first_name, is_main_admin = admin
                role = "👑 Главный" if is_main_admin else "🔹 Обычный"
                admin_list += f"{role} админ: {first_name or 'No name'}"
                if username:
                    admin_list += f" (@{username})"
                admin_list += f" | ID: {admin_id}\n"

            bot.send_message(user_id, admin_list)
            
        except Exception:
            logger.exception("Error in /admins handler: %s", message)

    @bot.message_handler(commands=['stats'])
    def stats_command(message):
        """Показывает статистику пользователей (для всех админов)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для администраторов.")
                return

            count = get_user_count()
            bot.send_message(user_id, f"📊 Статистика бота:\n\n👥 Всего пользователей: {count}")
            
        except Exception:
            logger.exception("Error in /stats handler: %s", message)

    @bot.message_handler(commands=['getusers'])
    def get_users_command(message):
        """Показывает список всех пользователей (для всех админов)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для администраторов.")
                return

            users = get_all_users()
            if not users:
                bot.send_message(user_id, "📝 База пользователей пуста.")
                return

            # Разбиваем на части если слишком много пользователей
            user_list = "👥 Список всех пользователей:\n\n"
            for user in users:
                user_id, username, first_name, last_name = user
                name = first_name or ""
                if last_name:
                    name += f" {last_name}"
                if not name.strip():
                    name = "No name"
                
                user_list += f"🆔 {user_id} | {name}"
                if username:
                    user_list += f" (@{username})"
                user_list += "\n"

                # Если сообщение становится слишком длинным, отправляем часть
                if len(user_list) > 3000:
                    bot.send_message(user_id, user_list)
                    user_list = ""

            if user_list:
                bot.send_message(user_id, user_list)
                
        except Exception:
            logger.exception("Error in /getusers handler: %s", message)

    @bot.message_handler(commands=['sendall'])
    def send_all_command(message):
        """Рассылка сообщения всем пользователям (для всех админов)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для администраторов.")
                return

            parts = message.text.split(' ', 1)
            if len(parts) < 2:
                bot.send_message(user_id, "❌ Используй: /sendall ваш_текст_рассылки\n\nПример:\n/sendall Важное обновление бота!")
                return

            broadcast_text = parts[1]
            users = get_all_users()
            
            if not users:
                bot.send_message(user_id, "❌ Нет пользователей для рассылки.")
                return

            bot.send_message(user_id, f"🔄 Начинаю рассылку для {len(users)} пользователей...")

            success_count = 0
            fail_count = 0
            
            for user in users:
                try:
                    bot.send_message(user[0], f"📢 Рассылка от администратора:\n\n{broadcast_text}")
                    success_count += 1
                    time.sleep(0.1)  # Задержка чтобы не превысить лимиты Telegram
                except Exception as e:
                    logger.error(f"Failed to send broadcast to {user[0]}: {e}")
                    fail_count += 1

            bot.send_message(user_id, f"✅ Рассылка завершена:\n\n"
                                     f"✅ Успешно: {success_count}\n"
                                     f"❌ Не удалось: {fail_count}")
            
        except Exception:
            logger.exception("Error in /sendall handler: %s", message)

    # ==================== СТАРЫЕ КОМАНДЫ (остаются без изменений) ====================

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
            admin_text = f"📞 Пользователь {message.from_user.first_name} "
            admin_text += f"@{message.from_user.username or 'без username'} "
            admin_text += f"(ID: {user_id}) просит связаться."
            
            # Отправляем всем админам
            admins = get_all_admins()
            for admin in admins:
                try:
                    bot.send_message(admin[0], admin_text)
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin[0]}: {e}")
            
            # Запускаем восстановление кнопки через 30 секунд
            Thread(target=restore_button, args=(user_id,), daemon=True).start()
            
        except Exception:
            logger.exception("Error in contact request handler: %s", message)

    @bot.message_handler(commands=['reply'])
    def start_reply_mode(message):
        try:
            user_id = int(message.from_user.id)
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для администратора.")
                return

            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(user_id, "❌ Используй: /reply user_id\nПример: /reply 123456789")
                return

            try:
                target_id = int(parts[1])
            except ValueError:
                bot.send_message(user_id, "❌ Неверный user_id. Это должно быть целое число.")
                return

            user_reply_mode[user_id] = target_id
            bot.send_message(user_id, f"🔹 Режим ответа включен для пользователя ID: {target_id}")
        except Exception:
            logger.exception("Error in /reply handler: %s", message)

    @bot.message_handler(commands=['stop'])
    def stop_reply_mode(message):
        try:
            user_id = int(message.from_user.id)
            if is_admin(user_id):
                if user_id in user_reply_mode:
                    del user_reply_mode[user_id]
                    bot.send_message(user_id, "🔹 Режим ответа выключен.")
                else:
                    bot.send_message(user_id, "🔹 Режим ответа не был включен.")
        except Exception:
            logger.exception("Error in /stop handler: %s", message)

    @bot.message_handler(func=lambda message: is_admin(int(message.from_user.id)) and int(message.from_user.id) in user_reply_mode)
    def handle_admin_reply(message):
        try:
            user_id = int(message.from_user.id)
            if message.content_type != 'text':
                bot.send_message(user_id, "❌ В режиме ответа можно отправлять только текст.")
                return

            target_user_id = user_reply_mode.get(user_id)
            if not target_user_id:
                bot.send_message(user_id, "❌ Целевой пользователь не найден.")
                return

            try:
                bot.send_message(target_user_id, f"💌 Поступил ответ от kvazador:\n\n{message.text}")
                bot.send_message(user_id, f"✅ Ответ отправлен пользователю ID: {target_user_id}")
            except Exception as e:
                logger.exception("Failed to send admin reply to %s: %s", target_user_id, e)
                bot.send_message(user_id, f"❌ Ошибка отправки: {e}")
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

            # Проверка кулдауна (кроме админов)
            if not is_admin(user_id):
                cooldown_remaining = check_cooldown(user_id)
                if cooldown_remaining > 0:
                    bot.send_message(
                        user_id, 
                        f"⏳ Пожалуйста, подождите {int(cooldown_remaining)} секунд перед отправкой следующего сообщения."
                    )
                    return

            if is_admin(user_id) and user_id not in user_reply_mode:
                bot.send_message(user_id, "ℹ️ Чтобы ответить пользователю, используй команду /reply user_id")
                return

            user_info = f"👤 От: {message.from_user.first_name}"
            if message.from_user.last_name:
                user_info += f" {message.from_user.last_name}"
            if message.from_user.username:
                user_info += f" (@{message.from_user.username})"
            user_info += f"\n🆔 ID: {user_id}"
            user_info += f"\n⏰ {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}"

            # Отправляем сообщение всем админам
            admins = get_all_admins()
            for admin in admins:
                try:
                    bot.send_message(admin[0], f"{user_info}\n\n📨 Сообщение:\n\n{message.text}")
                except Exception as e:
                    logger.error(f"Failed to forward message to admin {admin[0]}: {e}")

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

            # Проверка кулдауна (кроме админов)
            if not is_admin(user_id):
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

            # Отправка соответствующего типа медиа всем админам
            admins = get_all_admins()
            for admin in admins:
                try:
                    if message.photo:
                        bot.send_photo(admin[0], message.photo[-1].file_id, caption=caption)
                    elif message.voice:
                        bot.send_voice(admin[0], message.voice.file_id, caption=caption)
                    elif message.video:
                        bot.send_video(admin[0], message.video.file_id, caption=caption)
                    elif message.document:
                        bot.send_document(admin[0], message.document.file_id, caption=caption)
                    elif message.audio:
                        bot.send_audio(admin[0], message.audio.file_id, caption=caption)
                    else:
                        bot.send_message(admin[0], f"{user_info}\n📨 Прислал медиа, но тип не определён.")
                except Exception as e:
                    logger.error(f"Failed to forward media to admin {admin[0]}: {e}")

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

            # Проверка кулдауна (кроме админов)
            if not is_admin(user_id):
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

            # Отправляем всем админам
            admins = get_all_admins()
            for admin in admins:
                try:
                    if message.contact:
                        bot.send_contact(
                            admin[0],
                            phone_number=message.contact.phone_number,
                            first_name=message.contact.first_name,
                            last_name=getattr(message.contact, "last_name", None)
                        )
                        bot.send_message(admin[0], f"{user_info}\n📞 Прислал контакт")
                    elif message.location:
                        bot.send_location(
                            admin[0],
                            message.location.latitude,
                            message.location.longitude,
                        )
                        bot.send_message(admin[0], f"{user_info}\n📍 Прислал локацию")
                    else:
                        bot.send_message(admin[0], f"{user_info}\n📨 Прислал контакт/локацию, но детали отсутствуют.")
                except Exception as e:
                    logger.error(f"Failed to forward contact/location to admin {admin[0]}: {e}")

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
