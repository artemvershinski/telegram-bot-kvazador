#!/usr/bin/env python3
# coding: utf-8

import os
import time
import logging
import sqlite3
import datetime
from threading import Thread
import requests
import json

from flask import Flask
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ----------------------------
# Настройка логирования
# ----------------------------
LOGFILE = os.environ.get("BOT_LOGFILE", "bot.log")
ADMIN_LOGFILE = os.environ.get("ADMIN_LOGFILE", "admin_actions.log")

# Настройка основного логгера
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGFILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Настройка логгера для действий администраторов
admin_logger = logging.getLogger('admin_actions')
admin_logger.setLevel(logging.INFO)
admin_handler = logging.FileHandler(ADMIN_LOGFILE, encoding='utf-8')
admin_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
admin_logger.addHandler(admin_handler)
admin_logger.propagate = False

def log_admin_action(admin_id, admin_name, action, target_info=""):
    """Логирует действия администраторов"""
    log_message = f"ADMIN {admin_id} ({admin_name}) - {action}"
    if target_info:
        log_message += f" - {target_info}"
    admin_logger.info(log_message)

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

# Используем SQLite с сохранением в /tmp (сохраняется между деплоями в Render)
DB_PATH = "/tmp/users.db"

def init_db():
    """Создаёт таблицы, если их нет."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, is_main_admin BOOLEAN DEFAULT FALSE, date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS bans
                     (user_id INTEGER PRIMARY KEY, 
                      ban_type TEXT NOT NULL,
                      ban_duration_seconds INTEGER,
                      banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      ban_reason TEXT,
                      banned_by INTEGER,
                      unban_request_date TIMESTAMP)''')
        
        # Добавляем главного админа если его нет
        c.execute("INSERT OR IGNORE INTO admins (user_id, username, first_name, is_main_admin) VALUES (?, ?, ?, ?)",
                  (ADMIN_ID, "main_admin", "Main Admin", True))
        
        conn.commit()
        conn.close()
        logger.info("Database initialized at %s", DB_PATH)
        
        # Создаем бекап при инициализации
        create_backup()
        
    except Exception as e:
        logger.exception("Failed to initialize DB: %s", e)

def create_backup():
    """Создает бекап базы данных"""
    try:
        if os.path.exists(DB_PATH):
            # Просто логируем что база существует
            file_size = os.path.getsize(DB_PATH)
            logger.info("Database backup check - file exists, size: %s bytes", file_size)
        else:
            logger.warning("Database file not found for backup")
    except Exception as e:
        logger.error("Failed to create backup: %s", e)

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
    except Exception as e:
        logger.exception("Failed to register user %s: %s", user_id, e)

def is_admin(user_id):
    """Проверяет, является ли пользователь админом"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        return result is not None
    except Exception as e:
        logger.exception("Failed to check admin status for %s: %s", user_id, e)
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
    except Exception as e:
        logger.exception("Failed to check main admin status for %s: %s", user_id, e)
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
    except Exception as e:
        logger.exception("Failed to add admin %s: %s", user_id, e)
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
    except Exception as e:
        logger.exception("Failed to remove admin %s: %s", user_id, e)
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
    except Exception as e:
        logger.exception("Failed to get users list: %s", e)
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
    except Exception as e:
        logger.exception("Failed to get user count: %s", e)
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
    except Exception as e:
        logger.exception("Failed to get admins list: %s", e)
        return []

def get_admin_logs(admin_id=None, days=30):
    """Возвращает логи администраторов за указанный период"""
    try:
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        
        with open(ADMIN_LOGFILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        logs = []
        for line in lines:
            try:
                # Парсим строку лога
                parts = line.strip().split(' - ', 2)
                if len(parts) >= 3:
                    timestamp = parts[0]
                    log_data = parts[2]
                    
                    # Проверяем дату
                    log_datetime = datetime.datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S,%f')
                    if log_datetime >= datetime.datetime.now() - datetime.timedelta(days=days):
                        
                        # Если указан конкретный админ, фильтруем по нему
                        if admin_id:
                            # Ищем по ID админа в формате "ADMIN {admin_id}"
                            if f"ADMIN {admin_id}" in log_data:
                                logs.append(line.strip())
                            # Дополнительно ищем по username в логах ответов
                            else:
                                # Пытаемся получить username админа для поиска
                                try:
                                    admin_chat = bot.get_chat(int(admin_id))
                                    if admin_chat.username and f"@{admin_chat.username}" in log_data:
                                        logs.append(line.strip())
                                except:
                                    pass
                        else:
                            logs.append(line.strip())
            except Exception as e:
                continue
        
        return logs
    except Exception as e:
        logger.exception("Failed to read admin logs: %s", e)
        return []

# ==================== СИСТЕМА БАНОВ ====================

def ban_user(user_id, ban_type, duration_seconds=None, reason="", banned_by=None):
    """Банит пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        
        if ban_type == "permanent":
            c.execute('''INSERT OR REPLACE INTO bans 
                        (user_id, ban_type, ban_duration_seconds, ban_reason, banned_by) 
                        VALUES (?, ?, ?, ?, ?)''',
                     (user_id, ban_type, None, reason, banned_by))
        else:  # temporary
            c.execute('''INSERT OR REPLACE INTO bans 
                        (user_id, ban_type, ban_duration_seconds, ban_reason, banned_by) 
                        VALUES (?, ?, ?, ?, ?)''',
                     (user_id, ban_type, duration_seconds, reason, banned_by))
        
        conn.commit()
        conn.close()
        logger.info("Banned user %s: type=%s, duration=%s", user_id, ban_type, duration_seconds)
        return True
    except Exception as e:
        logger.exception("Failed to ban user %s: %s", user_id, e)
        return False

def unban_user(user_id):
    """Разбанивает пользователя"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        logger.info("Unbanned user %s", user_id)
        return True
    except Exception as e:
        logger.exception("Failed to unban user %s: %s", user_id, e)
        return False

def is_banned(user_id):
    """Проверяет, забанен ли пользователь и возвращает информацию о бане"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT ban_type, ban_duration_seconds, banned_at, ban_reason FROM bans WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            return None
        
        ban_type, duration_seconds, banned_at, reason = result
        
        # Для временного бана проверяем истекло ли время
        if ban_type == "temporary" and duration_seconds:
            banned_time = datetime.datetime.strptime(banned_at, '%Y-%m-%d %H:%M:%S')
            current_time = datetime.datetime.now()
            time_passed = (current_time - banned_time).total_seconds()
            
            if time_passed >= duration_seconds:
                # Время бана истекло - разбаниваем
                unban_user(user_id)
                return None
            else:
                time_left = duration_seconds - time_passed
                return {
                    'type': ban_type,
                    'time_left': time_left,
                    'reason': reason
                }
        
        # Для пермача или если время не истекло
        return {
            'type': ban_type,
            'reason': reason
        }
    except Exception as e:
        logger.exception("Failed to check ban status for %s: %s", user_id, e)
        return None

def format_time_left(seconds):
    """Форматирует оставшееся время в читаемый вид"""
    if seconds < 60:
        return f"{int(seconds)} секунд"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes} минут {secs} секунд"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours} часов {minutes} минут"

def can_request_unban(user_id):
    """Проверяет, может ли пользователь запросить разбан (прошла ли неделя с последнего запроса)"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT unban_request_date FROM bans WHERE user_id = ? AND ban_type = 'permanent'", (user_id,))
        result = c.fetchone()
        conn.close()
        
        if not result or not result[0]:
            return True  # Если даты запроса нет, можно запросить
        
        last_request = datetime.datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
        current_time = datetime.datetime.now()
        time_passed = (current_time - last_request).total_seconds()
        
        # 7 дней в секундах
        return time_passed >= 7 * 24 * 3600
    except Exception as e:
        logger.exception("Failed to check unban request for %s: %s", user_id, e)
        return False

def update_unban_request_date(user_id):
    """Обновляет дату последнего запроса на разбан"""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute("UPDATE bans SET unban_request_date = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.exception("Failed to update unban request date for %s: %s", user_id, e)
        return False

user_reply_mode = {}
user_unban_mode = {}

# ----------------------------
# Хэндлеры бота
# ----------------------------
if bot:
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        try:
            user_id = int(message.from_user.id)
            
            # Проверяем бан
            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда. Для разбана используйте /unban")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены. До разбана осталось: {time_left}")
                return

            register_user(user_id,
                          message.from_user.username,
                          message.from_user.first_name,
                          message.from_user.last_name)

            welcome_text = (
                "Привет. Я бот-пересыльщик сообщений для kvazador.\n\n"
                "Для связи с kvazador сначала вам необходимо отправить сообщение (сколько потребуется) здесь. "
                "Ответ может поступить через данного бота, либо вам в ЛС.\n\n"
                "Ваше сообщение будет доставлено ему от вашего имени.\n\n"
                "Сам kvazador свяжется с вами как только заметит ваше сообщение в боте. "
            )

            markup = ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(KeyboardButton("📞 Попросить связаться со мной."))
            bot.send_message(user_id, welcome_text, reply_markup=markup)
        except Exception:
            logger.exception("Error in /start handler for message: %s", message)

    # ==================== КОМАНДЫ БАНОВ ====================

    @bot.message_handler(commands=['ban'])
    def ban_command(message):
        """Временный бан пользователя"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для администраторов.")
                return

            parts = message.text.split()
            if len(parts) < 4:
                bot.send_message(user_id, "❌ Используй: /ban user_id время_в_секундах причина\n\nПример:\n/ban 123456789 3600 Спам\n/ban 123456789 86400 Оскорбления")
                return

            try:
                target_id = int(parts[1])
                duration = int(parts[2])
                reason = ' '.join(parts[3:])
            except ValueError:
                bot.send_message(user_id, "❌ Неверный формат. user_id и время должны быть числами.")
                return

            if duration <= 0:
                bot.send_message(user_id, "❌ Время бана должно быть положительным числом.")
                return

            # Нельзя забанить админа
            if is_admin(target_id):
                bot.send_message(user_id, "❌ Нельзя забанить администратора.")
                return

            if ban_user(target_id, "temporary", duration, reason, user_id):
                # Уведомляем пользователя о бане
                try:
                    duration_text = format_time_left(duration)
                    bot.send_message(target_id, f"🚫 Вы были забанены на {duration_text}.\nПричина: {reason}")
                except Exception as e:
                    logger.warning("Could not notify banned user %s: %s", target_id, e)

                bot.send_message(user_id, f"✅ Пользователь {target_id} забанен на {format_time_left(duration)}.\nПричина: {reason}")
                
                # Логируем действие
                admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                log_admin_action(user_id, admin_name, "временный бан", f"пользователь: {target_id}, время: {duration}сек, причина: {reason}")
            else:
                bot.send_message(user_id, "❌ Ошибка при бане пользователя.")
                
        except Exception:
            logger.exception("Error in /ban handler: %s", message)

    @bot.message_handler(commands=['spermban'])
    def permanent_ban_command(message):
        """Перманентный бан пользователя"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для администраторов.")
                return

            parts = message.text.split()
            if len(parts) < 3:
                bot.send_message(user_id, "❌ Используй: /spermban user_id причина\n\nПример:\n/spermban 123456789 Спам\n/spermban 123456789 Оскорбления")
                return

            try:
                target_id = int(parts[1])
                reason = ' '.join(parts[2:])
            except ValueError:
                bot.send_message(user_id, "❌ Неверный user_id. Это должно быть целое число.")
                return

            # Нельзя забанить админа
            if is_admin(target_id):
                bot.send_message(user_id, "❌ Нельзя забанить администратора.")
                return

            if ban_user(target_id, "permanent", None, reason, user_id):
                # Уведомляем пользователя о бане
                try:
                    bot.send_message(target_id, f"🚫 Вы были забанены навсегда.\nПричина: {reason}\n\nДля запроса разбана используйте /unban")
                except Exception as e:
                    logger.warning("Could not notify banned user %s: %s", target_id, e)

                bot.send_message(user_id, f"✅ Пользователь {target_id} забанен навсегда.\nПричина: {reason}")
                
                # Логируем действие
                admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                log_admin_action(user_id, admin_name, "перманентный бан", f"пользователь: {target_id}, причина: {reason}")
            else:
                bot.send_message(user_id, "❌ Ошибка при бане пользователя.")
                
        except Exception:
            logger.exception("Error in /spermban handler: %s", message)

    @bot.message_handler(commands=['unban'])
    def unban_request_command(message):
        """Запрос разбана от пользователя (только для пермаченных)"""
        try:
            user_id = int(message.from_user.id)
            
            # Проверяем бан
            ban_info = is_banned(user_id)
            if not ban_info or ban_info['type'] != 'permanent':
                bot.send_message(user_id, "❌ Эта команда только для перманентно забаненных пользователей.")
                return

            # Проверяем можно ли запросить разбан (прошла ли неделя)
            if not can_request_unban(user_id):
                bot.send_message(user_id, "❌ Вы уже отправляли запрос на разбан. Следующая попытка будет доступна через неделю после последнего запроса.")
                return

            # Включаем режим запроса разбана
            user_unban_mode[user_id] = True
            bot.send_message(user_id, "✍️ Напишите сообщение для модераторов, почему мы должны вас разбанить. Постарайтесь, ведь следующая попытка будет только через неделю.")
            
        except Exception:
            logger.exception("Error in /unban handler: %s", message)

    @bot.message_handler(commands=['obossat'])
    def unban_command(message):
        """Разбан пользователя администратором"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для администраторов.")
                return

            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(user_id, "❌ Используй: /obossat user_id\n\nПример:\n/obossat 123456789")
                return

            try:
                target_id = int(parts[1])
            except ValueError:
                bot.send_message(user_id, "❌ Неверный user_id. Это должно быть целое число.")
                return

            # Проверяем забанен ли пользователь
            ban_info = is_banned(target_id)
            if not ban_info:
                bot.send_message(user_id, f"ℹ️ Пользователь {target_id} не забанен.")
                return

            if unban_user(target_id):
                # Уведомляем пользователя о разбане
                unban_message = "✅ Вы были разбанены. Больше не нарушайте правила!"
                if len(parts) > 2:
                    unban_message = ' '.join(parts[2:])
                
                try:
                    bot.send_message(target_id, unban_message)
                except Exception as e:
                    logger.warning("Could not notify unbanned user %s: %s", target_id, e)

                bot.send_message(user_id, f"✅ Пользователь {target_id} разбанен.")
                
                # Логируем действие
                admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                log_admin_action(user_id, admin_name, "разбан пользователя", f"пользователь: {target_id}")
            else:
                bot.send_message(user_id, "❌ Ошибка при разбане пользователя.")
                
        except Exception:
            logger.exception("Error in /obossat handler: %s", message)

    # Обработчик для сообщений в режиме запроса разбана
    @bot.message_handler(func=lambda message: int(message.from_user.id) in user_unban_mode and user_unban_mode[int(message.from_user.id)])
    def handle_unban_request(message):
        try:
            user_id = int(message.from_user.id)
            
            if message.content_type != 'text':
                bot.send_message(user_id, "❌ Пожалуйста, отправьте текстовое сообщение.")
                return

            # Отправляем запрос всем админам
            user_info = f"👤 Пользователь {message.from_user.first_name}"
            if message.from_user.username:
                user_info += f" (@{message.from_user.username})"
            user_info += f" (ID: {user_id}) запрашивает разбан:\n\n{message.text}"

            admins = get_all_admins()
            for admin in admins:
                try:
                    bot.send_message(admin[0], user_info)
                except Exception as e:
                    logger.error(f"Failed to send unban request to admin {admin[0]}: {e}")

            # Обновляем дату запроса
            update_unban_request_date(user_id)
            
            # Выключаем режим запроса
            user_unban_mode[user_id] = False
            
            bot.send_message(user_id, "✅ Ваш запрос на разбан отправлен модераторам. Следующая попытка будет доступна через неделю.")
            
        except Exception:
            logger.exception("Error in unban request handler: %s", message)

    # ==================== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ ЛОГАМИ ====================

    @bot.message_handler(commands=['clearlogs'])
    def clear_logs_command(message):
        """Очищает логи администраторов (только для главного админа)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            parts = message.text.split()
            if len(parts) < 2:
                bot.send_message(user_id, "❌ Используй:\n/clearlogs all - очистить все логи\n/clearlogs 123456789 - очистить логи конкретного админа")
                return

            target = parts[1]
            
            if target == 'all':
                # Очищаем все логи
                open(ADMIN_LOGFILE, 'w', encoding='utf-8').close()
                bot.send_message(user_id, "✅ Все логи администраторов очищены.")
                
                # Логируем действие
                admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                log_admin_action(user_id, admin_name, "очистка всех логов")
                
            else:
                try:
                    target_id = int(target)
                    # Удаляем логи только для указанного админа
                    logs = get_admin_logs(None, 36500)  # 100 лет = все логи
                    
                    # Получаем username админа для поиска в логах
                    admin_username = None
                    try:
                        admin_chat = bot.get_chat(target_id)
                        admin_username = f"@{admin_chat.username}" if admin_chat.username else None
                    except:
                        pass
                    
                    # Фильтруем логи - удаляем те, где есть ID админа ИЛИ его username
                    filtered_logs = []
                    for log in logs:
                        if f"ADMIN {target_id}" in log:
                            continue  # Пропускаем логи с ID админа
                        if admin_username and admin_username in log:
                            continue  # Пропускаем логи с username админа
                        filtered_logs.append(log)
                    
                    # Перезаписываем файл без логов этого админа
                    with open(ADMIN_LOGFILE, 'w', encoding='utf-8') as f:
                        for log in filtered_logs:
                            f.write(log + '\n')
                    
                    bot.send_message(user_id, f"✅ Логи администратора {target_id} очищены.")
                    
                    # Логируем действие
                    admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                    log_admin_action(user_id, admin_name, "очистка логов администратора", f"админ: {target_id}")
                    
                except ValueError:
                    bot.send_message(user_id, "❌ Неверный user_id. Используй число или 'all'")
                    
        except Exception:
            logger.exception("Error in /clearlogs handler: %s", message)

    @bot.message_handler(commands=['adminlogs'])
    def show_admin_logs(message):
        """Показывает логи администраторов (только для главного админа)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            parts = message.text.split()
            days = 30  # по умолчанию за последний месяц
            
            # Парсим параметры
            target_admin_id = None
            if len(parts) >= 2:
                # Проверяем, является ли первый параметр ID админа
                try:
                    target_admin_id = int(parts[1])
                except ValueError:
                    # Если не число, проверяем ключевые слова
                    if parts[1].lower() == 'all':
                        target_admin_id = None
                    else:
                        bot.send_message(user_id, "❌ Используй:\n"
                                                "/adminlogs - логи всех админов за месяц\n"
                                                "/adminlogs all - то же самое\n"
                                                "/adminlogs 123456789 - логи конкретного админа\n"
                                                "/adminlogs 123456789 7 - логи админа за 7 дней")
                        return
            
            # Проверяем количество дней
            if len(parts) >= 3:
                try:
                    days = int(parts[2])
                    if days <= 0 or days > 365:
                        bot.send_message(user_id, "❌ Количество дней должно быть от 1 до 365")
                        return
                except ValueError:
                    bot.send_message(user_id, "❌ Количество дней должно быть числом")
                    return

            bot.send_message(user_id, f"🔄 Получаю логи за последние {days} дней...")

            logs = get_admin_logs(target_admin_id, days)
            
            if not logs:
                if target_admin_id:
                    bot.send_message(user_id, f"📭 Логов для администратора {target_admin_id} за последние {days} дней не найдено.")
                else:
                    bot.send_message(user_id, f"📭 Логов администраторов за последние {days} дней не найдено.")
                return

            # Формируем текст логов
            if target_admin_id:
                log_text = f"📊 Логи администратора {target_admin_id} за последние {days} дней:\n\n"
            else:
                log_text = f"📊 Логи всех администраторов за последние {days} дней:\n\n"

            # Группируем логи по датам
            date_groups = {}
            for log in logs:
                try:
                    date_part = log.split(' ')[0]  # Берем только дату
                    if date_part not in date_groups:
                        date_groups[date_part] = []
                    date_groups[date_part].append(log)
                except:
                    continue

            # Обрабатываем логи для каждого дня
            for date, date_logs in sorted(date_groups.items(), reverse=True):
                log_text += f"📅 {date}:\n"
                
                for log in date_logs:
                    # Парсим лог
                    log_parts = log.split(' - ', 2)
                    if len(log_parts) >= 3:
                        time_part = log_parts[0].split(' ')[1][:8]  # Берем только время
                        admin_part = log_parts[1]
                        action_part = log_parts[2]
                        
                        # Извлекаем информацию об админе
                        admin_info = admin_part.replace('ADMIN ', '')
                        
                        # Форматируем действие
                        formatted_action = action_part
                        
                        # Убираем логирование включения/выключения режима ответа
                        if "включение режима ответа" in action_part or "выключение режима ответа" in action_part:
                            continue
                        
                        # Форматируем отправку ответа пользователю
                        if "отправка ответа пользователю" in action_part:
                            # Парсим информацию об ответе
                            if "пользователь:" in action_part and "ответ:" in action_part:
                                user_part = action_part.split("пользователь: ")[1].split(" | ")[0]
                                response_text = action_part.split("ответ: ")[1]
                                
                                # Пытаемся получить username админа
                                admin_id = admin_info.split(' ')[0]
                                admin_username = "Неизвестно"
                                try:
                                    admin_chat = bot.get_chat(int(admin_id))
                                    admin_username = f"@{admin_chat.username}" if admin_chat.username else admin_chat.first_name
                                except:
                                    admin_username = f"ID: {admin_id}"
                                
                                # Пытаемся получить username пользователя
                                target_username = "Неизвестно"
                                try:
                                    target_chat = bot.get_chat(int(user_part))
                                    target_username = f"@{target_chat.username}" if target_chat.username else target_chat.first_name
                                except:
                                    target_username = f"ID: {user_part}"
                                
                                formatted_action = f"Администратор {admin_username} ответил пользователю {target_username}\nОтвет: {response_text}"
                        
                        # Форматируем добавление администратора
                        elif "добавление администратора" in action_part:
                            if "новый админ:" in action_part:
                                new_admin_info = action_part.split("новый админ: ")[1]
                                formatted_action = f"добавление администратора - новый админ: {new_admin_info}"
                        
                        # Форматируем удаление администратора  
                        elif "удаление администратора" in action_part:
                            if "удален админ:" in action_part:
                                removed_admin_id = action_part.split("удален админ: ")[1]
                                formatted_action = f"удаление администратора - удален админ: {removed_admin_id}"
                        
                        # Форматируем рассылку сообщений
                        elif "рассылка сообщений" in action_part:
                            if "получателей:" in action_part:
                                stats = action_part.split("рассылка сообщений - ")[1]
                                formatted_action = f"рассылка сообщений - {stats}"
                        
                        # Форматируем просмотр статистики
                        elif "просмотр статистики" in action_part:
                            formatted_action = "просмотр статистики"
                        
                        # Форматируем просмотр списка пользователей
                        elif "просмотр списка пользователей" in action_part:
                            formatted_action = "просмотр списка пользователей"
                        
                        # Форматируем просмотр списка администраторов
                        elif "просмотр списка администраторов" in action_part:
                            formatted_action = "просмотр списка администраторов"
                        
                        # Форматируем баны
                        elif "временный бан" in action_part or "перманентный бан" in action_part or "разбан пользователя" in action_part:
                            formatted_action = action_part
                        
                        log_text += f"{time_part} - {formatted_action}\n"
                
                log_text += "\n"

                # Если сообщение становится слишком длинным, отправляем часть
                if len(log_text) > 3500:
                    bot.send_message(user_id, log_text)
                    log_text = ""

            if log_text:
                bot.send_message(user_id, log_text)

            # Статистика
            bot.send_message(user_id, f"📈 Всего записей: {len(logs)}")

            # Логируем запрос логов (только для главного админа)
            if is_main_admin(user_id):
                admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                action = f"просмотр логов за {days} дней"
                target_info = f"админ {target_admin_id}" if target_admin_id else "все админы"
                log_admin_action(user_id, admin_name, action, target_info)
            
        except Exception:
            logger.exception("Error in /adminlogs handler: %s", message)

    @bot.message_handler(commands=['logstats'])
    def show_log_statistics(message):
        """Показывает статистику по логам администраторов"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            parts = message.text.split()
            days = 30  # по умолчанию за последний месяц
            
            if len(parts) >= 2:
                try:
                    days = int(parts[1])
                    if days <= 0 or days > 365:
                        bot.send_message(user_id, "❌ Количество дней должно быть от 1 до 365")
                        return
                except ValueError:
                    bot.send_message(user_id, "❌ Количество дней должно быть числом")
                    return

            bot.send_message(user_id, f"🔄 Анализирую логи за последние {days} дней...")

            logs = get_admin_logs(None, days)
            
            if not logs:
                bot.send_message(user_id, f"📭 Логов администраторов за последние {days} дней не найдено.")
                return

            # Анализируем логи
            admin_actions = {}
            action_types = {}
            
            for log in logs:
                try:
                    # Парсим строку лога для извлечения ID админа и действия
                    parts = log.split(' - ')
                    if len(parts) >= 3:
                        admin_part = parts[1]
                        action_part = parts[2]
                        
                        # Извлекаем ID админа
                        admin_id = admin_part.split(' ')[1]
                        
                        # Считаем действия по админам
                        if admin_id not in admin_actions:
                            admin_actions[admin_id] = 0
                        admin_actions[admin_id] += 1
                        
                        # Считаем типы действий
                        action_type = action_part.split(' - ')[0] if ' - ' in action_part else action_part
                        if action_type not in action_types:
                            action_types[action_type] = 0
                        action_types[action_type] += 1
                except:
                    continue

            # Формируем статистику
            stats_text = f"📊 Статистика логов администраторов за {days} дней:\n\n"
            stats_text += f"📈 Всего записей: {len(logs)}\n\n"
            
            stats_text += "👥 Активность по администраторам:\n"
            for admin_id, count in sorted(admin_actions.items(), key=lambda x: x[1], reverse=True):
                # Пытаемся получить имя админа
                admin_name = "Неизвестно"
                try:
                    admin_chat = bot.get_chat(int(admin_id))
                    admin_name = f"@{admin_chat.username}" if admin_chat.username else admin_chat.first_name
                except:
                    admin_name = f"ID: {admin_id}"
                
                stats_text += f"• {admin_name}: {count} действий\n"
            
            stats_text += "\n📋 Типы действий:\n"
            for action_type, count in sorted(action_types.items(), key=lambda x: x[1], reverse=True):
                stats_text += f"• {action_type}: {count} раз\n"

            bot.send_message(user_id, stats_text)

            # Логируем запрос статистики
            admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
            log_admin_action(user_id, admin_name, f"просмотр статистики логов за {days} дней")
            
        except Exception:
            logger.exception("Error in /logstats handler: %s", message)

    # ==================== СИСТЕМА АДМИНИСТРИРОВАНИЯ ====================

    @bot.message_handler(commands=['addadmin'])
    def add_admin_command(message):
        """Добавляет обычного админа (только для главного админа)"""
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для ГА.")
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
                bot.send_message(user_id, "❌ Вы уже ГА.")
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
                
                # Логируем действие
                admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                log_admin_action(user_id, admin_name, "добавление администратора", f"новый админ: {target_id} ({first_name})")
                
                # Уведомляем нового админа
                try:
                    bot.send_message(target_id, "🎉 Вы были назначены администратором бота!\n\n"
                                                "Теперь вам доступны команды:\n"
                                                "/stats - статистика пользователей\n"
                                                "/getusers - список всех пользователей\n"
                                                "/sendall - рассылка сообщений\n"
                                                "/ban - временный бан\n"
                                                "/spermban - перманентный бан\n"
                                                "/obossat - разбан")
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
                bot.send_message(user_id, "❌ Эта команда только для ГА")

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
                bot.send_message(ADMIN_ID, f"✅ Администратор (ID: {target_id}) удален.")
                
                # Логируем действие
                admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                log_admin_action(user_id, admin_name, "удаление администратора", f"удален админ: {target_id}")
                
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
            
            # Логируем действие
            admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
            log_admin_action(user_id, admin_name, "просмотр списка администраторов")
            
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
            
            # Получаем статистику по банам
            try:
                conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM bans WHERE ban_type = 'permanent'")
                permanent_bans = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM bans WHERE ban_type = 'temporary'")
                temporary_bans = c.fetchone()[0]
                conn.close()
            except Exception as e:
                logger.error("Failed to get ban stats: %s", e)
                permanent_bans = 0
                temporary_bans = 0

            stats_text = f"📊 Статистика бота:\n\n👥 Всего пользователей: {count}\n"
            stats_text += f"🚫 Перманентно забанено: {permanent_bans}\n"
            stats_text += f"⏳ Временно забанено: {temporary_bans}"
            
            bot.send_message(user_id, stats_text)
            
            # Логируем действие
            admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
            log_admin_action(user_id, admin_name, "просмотр статистики")
            
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
                
            # Логируем действие
            admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
            log_admin_action(user_id, admin_name, "просмотр списка пользователей")
                
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
                    # Пропускаем забаненных пользователей
                    if is_banned(user[0]):
                        continue
                        
                    bot.send_message(user[0], f"{broadcast_text}")
                    success_count += 1
                    time.sleep(0.1)  # Задержка чтобы не превысить лимиты Telegram
                except Exception as e:
                    logger.error(f"Failed to send broadcast to {user[0]}: {e}")
                    fail_count += 1

            bot.send_message(user_id, f"✅ Рассылка завершена:\n\n"
                                     f"✅ Успешно: {success_count}\n"
                                     f"❌ Не удалось: {fail_count}\n"
                                     f"🚫 Пропущено (забанены): {len(users) - success_count - fail_count}")
            
            # Логируем действие
            admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
            log_admin_action(user_id, admin_name, "рассылка сообщений", f"получателей: {len(users)}, успешно: {success_count}")
            
        except Exception:
            logger.exception("Error in /sendall handler: %s", message)

    # ==================== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ====================

    @bot.message_handler(func=lambda message: message.text == "📞 Попросить связаться со мной.")
    def handle_contact_request(message):
        try:
            user_id = int(message.from_user.id)
            
            # Проверяем бан
            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда и не можете использовать эту функцию.")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены и не можете использовать эту функцию. До разбана осталось: {time_left}")
                return
            
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
                f"🕒 Кнопка связи появится снова через {BUTTON_COOLDOWN} секунд",
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
            
            # НЕ логируем включение режима ответа
            
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
                    
                    # НЕ логируем выключение режима ответа
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

            # Проверяем не забанен ли пользователь
            if is_banned(target_user_id):
                bot.send_message(user_id, "❌ Нельзя отправить сообщение забаненному пользователю.")
                return

            try:
                # Отправляем ответ пользователю
                bot.send_message(target_user_id, f"💌 Поступил ответ от kvazador:\n\n{message.text}")
                bot.send_message(user_id, f"✅ Ответ отправлен пользователю ID: {target_user_id}")
                
                # Логируем отправку ответа с текстом
                admin_name = f"{message.from_user.first_name} (@{message.from_user.username})" if message.from_user.username else message.from_user.first_name
                log_admin_action(user_id, admin_name, f"отправка ответа пользователю - пользователь: {target_user_id} | ответ: {message.text}")
                
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

            # Проверяем бан
            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда. Для разбана используйте /unban")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены. До разбана осталось: {time_left}")
                return

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

            # Проверяем бан
            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда и не можете отправлять медиа.")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены и не можете отправлять медиа. До разбана осталось: {time_left}")
                return

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

            # Проверяем бан
            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда и не можете отправлять контакты/локации.")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены и не можете отправлять контакты/локации. До разбана осталось: {time_left}")
                return

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
