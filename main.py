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
from collections import defaultdict

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

def get_current_time():
    """Возвращает текущее время в формате UTC"""
    return datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def ensure_log_files():
    """Создает файлы логов если они не существуют"""
    try:
        for log_file in [LOGFILE, ADMIN_LOGFILE]:
            if not os.path.exists(log_file):
                open(log_file, 'w', encoding='utf-8').close()
                logger.info(f"Created log file: {log_file}")
    except Exception as e:
        logger.error(f"Failed to create log files: {e}")

def format_admin_name(user):
    """Форматирует имя администратора для логов"""
    if user.username:
        return f"@{user.username}"
    return user.first_name or "Unknown"

def format_target_info(user_id, username=None, first_name=None):
    """Форматирует информацию о цели для логов"""
    #  4ИСТКА ДУБЛЕЙ "@"
    if username and "@" in username:
        username = username.replace("@@", "@").lstrip("@")
        username = f"@{username}" if username else "Неизвестно"
    
    if username:
        return f"{username} ({user_id})"
    elif first_name:
        return f"{first_name} ({user_id})"
    else:
        return f"ID: {user_id}"

def log_admin_action(admin_user, action, target_info="", additional_info=""):
    """Логирует действия администраторов в новом формате"""
    try:
        admin_name = format_admin_name(admin_user)
        
        # ЧИСТИМ ДУБЛИ В target_info
        if target_info and "@@" in target_info:
            target_info = target_info.replace("@@", "@")
        
        log_message = f"{admin_name} {action}"
        
        if target_info:
            log_message += f" {target_info}"
        
        if additional_info:
            log_message += f" {additional_info}"
        
        logger.info(f"ADMIN_ACTION: {log_message}")
        admin_logger.info(log_message)
        
    except Exception as e:
        logger.error(f"Failed to log admin action: {e}")

def log_user_action(user, action, target_info="", additional_info=""):
    """Логирует действия пользователей"""
    try:
        user_name = format_admin_name(user)  # Используем ту же функцию форматирования
        log_message = f"{user_name} {action}"
        
        if target_info:
            log_message += f" {target_info}"
        
        if additional_info:
            log_message += f" {additional_info}"
        
        logger.info(f"USER_ACTION: {log_message}")
        
    except Exception as e:
        logger.error(f"Failed to log user action: {e}")

# ----------------------------
# Функции для чтения и форматирования логов
# ----------------------------

def parse_log_line(line):
    """Парсит строку лога и возвращает компоненты"""
    try:
        if ' - ' in line:
            parts = line.split(' - ', 1)
            timestamp_str = parts[0].strip()
            content = parts[1].strip()
            
            # Убираем миллисекунды если есть
            if ',' in timestamp_str:
                timestamp_str = timestamp_str.split(',')[0]
            
            return timestamp_str, content
        return None, None
    except Exception as e:
        logger.error(f"Error parsing log line: {line} - {e}")
        return None, None

def group_logs_by_date(logs):
    """Группирует логи по датам"""
    grouped = defaultdict(list)
    
    for log in logs:
        timestamp_str, content = parse_log_line(log)
        if timestamp_str and content:
            date_part = timestamp_str.split()[0]  # Берем только дату
            time_part = timestamp_str.split()[1] if ' ' in timestamp_str else "00:00:00"
            grouped[date_part].append((time_part, content))
    
    return grouped

def format_admin_logs_for_display(logs, days=30):
    """Форматирует логи администраторов для отображения"""
    if not logs:
        return "Логов не найдено"
    
    grouped_logs = group_logs_by_date(logs)
    
    if not grouped_logs:
        return "Логов не найдено"
    
    result = ""
    
    # Сортируем даты в обратном порядке (сначала новые)
    sorted_dates = sorted(grouped_logs.keys(), reverse=True)
    
    for date in sorted_dates:
        result += f"============={date}=============\n"
        
        day_logs = grouped_logs[date]
        # Сортируем логи по времени внутри дня
        day_logs.sort(key=lambda x: x[0])
        
        for i, (time_part, content) in enumerate(day_logs, 1):
            # Форматируем время (убираем секунды если нужно)
            display_time = time_part
            if len(display_time) > 8:
                display_time = display_time[:8]
            
            # Форматируем содержание лога
            formatted_content = format_log_content(content)
            
            result += f"{i}. {display_time} - {formatted_content}\n"
        
        result += "\n"
    
    return result

def format_log_content(content):
    """Форматирует содержание лога в нужный формат"""
    # Обработка логов администраторов
    if "ADMIN" in content:
        # Убираем префикс ADMIN и ID
        content = content.replace("ADMIN ", "")
        
        # Извлекаем компоненты
        if " - " in content:
            admin_part, action_part = content.split(" - ", 1)
            
            # Обрабатываем часть с администратором
            if "(" in admin_part and ")" in admin_part:
                admin_id = admin_part.split(" ")[0]
                admin_name = admin_part.split("(")[1].split(")")[0]
            else:
                admin_name = admin_part
                
            # Форматируем действия
            formatted_action = format_admin_action(action_part)
            return f"{admin_name} {formatted_action}"
    
    # Обработка пользовательских логов
    return content

def format_admin_action(action):
    """Форматирует действие администратора"""
    action_lower = action.lower()
    
    # Временный бан
    if "временный бан" in action_lower:
        return extract_ban_info(action, "ban")
    
    # Перманентный бан
    elif "перманентный бан" in action_lower:
        return extract_ban_info(action, "permban")
    
    # Разбан
    elif "разбан" in action_lower or "obossat" in action_lower:
        return extract_simple_action(action, "obossat")
    
    # Ответ пользователю
    elif "отправка ответа пользователю" in action_lower or "ответ" in action_lower:
        return extract_reply_info(action)
    
    # Добавление администратора
    elif "добавление администратора" in action_lower:
        return extract_admin_management(action, "addadmin")
    
    # Удаление администратора
    elif "удаление администратора" in action_lower:
        return extract_admin_management(action, "removeadmin")
    
    # Просмотр логов
    elif "просмотр логов" in action_lower:
        return extract_log_view(action)
    
    # Просмотр статистики
    elif "просмотр статистики" in action_lower:
        return "logstats"
    
    # Просмотр списка
    elif "просмотр списка" in action_lower:
        if "пользователей" in action_lower:
            return "getusers"
        elif "администраторов" in action_lower:
            return "admins"
    
    # Рассылка
    elif "рассылка" in action_lower:
        return extract_broadcast_info(action)
    
    # Очистка логов
    elif "очистка" in action_lower:
        return extract_log_clear(action)
    
    # По умолчанию возвращаем оригинальное действие
    return action

def extract_ban_info(action, ban_type):
    """Извлекает информацию о бане"""
    try:
        # Ищем пользователя
        user_part = None
        if "пользователь:" in action:
            user_part = action.split("пользователь:")[1].split(",")[0].strip()
        elif "user:" in action:
            user_part = action.split("user:")[1].split(",")[0].strip()
        
        # Ищем время
        time_part = ""
        if ban_type == "ban" and "время:" in action:
            time_part = action.split("время:")[1].split(",")[0].strip()
            if "сек" in time_part:
                time_part = time_part.replace("сек", "сек")
        
        # Ищем причину
        reason_part = ""
        if "причина:" in action:
            reason_part = action.split("причина:")[1].strip()
        elif "reason:" in action:
            reason_part = action.split("reason:")[1].strip()
        
        # ЧИСТИМ ДУБЛИ "@"
        if user_part and "@@" in user_part:
            user_part = user_part.replace("@@", "@")
        
        result = f"{ban_type} {user_part}"
        if time_part:
            result += f" [{time_part}]"
        if reason_part:
            result += f" [{reason_part}]"
        
        return result
        
    except Exception as e:
        logger.error(f"Error extracting ban info: {e}")
        return f"{ban_type} [error parsing]"

def extract_simple_action(action, action_type):
    """Извлекает информацию о простом действии"""
    try:
        if "пользователь:" in action:
            user_part = action.split("пользователь:")[1].strip()
            # ЧИСТИМ ДУБЛИ "@"
            if "@@" in user_part:
                user_part = user_part.replace("@@", "@")
            return f"{action_type} {user_part}"
        elif "user:" in action:
            user_part = action.split("user:")[1].strip()
            # ЧИСТИМ ДУБЛИ "@"
            if "@@" in user_part:
                user_part = user_part.replace("@@", "@")
            return f"{action_type} {user_part}"
        else:
            return action_type
    except Exception as e:
        logger.error(f"Error extracting simple action: {e}")
        return action_type

def extract_reply_info(action):
    """Извлекает информацию об ответе"""
    try:
        if "пользователь:" in action and "ответ:" in action:
            user_part = action.split("пользователь:")[1].split("|")[0].strip()
            reply_part = action.split("ответ:")[1].strip()
            # ЧИСТИМ ДУБЛИ "@"
            if "@@" in user_part:
                user_part = user_part.replace("@@", "@")
            return f"reply {user_part} [{reply_part}]"
        else:
            return "reply [unknown]"
    except Exception as e:
        logger.error(f"Error extracting reply info: {e}")
        return "reply [error parsing]"

def extract_admin_management(action, action_type):
    """Извлекает информацию об управлении админами"""
    try:
        if "админ:" in action:
            admin_part = action.split("админ:")[1].strip()
            # ЧИСТИМ ДУБЛИ "@"
            if "@@" in admin_part:
                admin_part = admin_part.replace("@@", "@")
            return f"{action_type} {admin_part}"
        elif "new admin:" in action:
            admin_part = action.split("new admin:")[1].strip()
            # ЧИСТИМ ДУБЛИ "@"
            if "@@" in admin_part:
                admin_part = admin_part.replace("@@", "@")
            return f"{action_type} {admin_part}"
        elif "удален админ:" in action:
            admin_part = action.split("удален админ:")[1].strip()
            # ЧИСТИМ ДУБЛИ "@"
            if "@@" in admin_part:
                admin_part = admin_part.replace("@@", "@")
            return f"{action_type} {admin_part}"
        else:
            return action_type
    except Exception as e:
        logger.error(f"Error extracting admin management: {e}")
        return action_type

def extract_log_view(action):
    """Извлекает информацию о просмотре логов"""
    try:
        if "админ" in action and "все админы" in action:
            days = action.split("за")[1].split("дней")[0].strip()
            return f"adminlogs all [{days} дней]"
        elif "админ" in action:
            admin_id = action.split("админ")[1].strip()
            days = action.split("за")[1].split("дней")[0].strip()
            return f"adminlogs {admin_id} [{days} дней]"
        else:
            return "adminlogs"
    except Exception as e:
        logger.error(f"Error extracting log view: {e}")
        return "adminlogs"

def extract_broadcast_info(action):
    """Извлекает информацию о рассылке"""
    try:
        if "получателей:" in action:
            users_part = action.split("получателей:")[1].split(",")[0].strip()
            success_part = action.split("успешно:")[1].strip()
            return f"sendall [users: {users_part}, success: {success_part}]"
        else:
            return "sendall"
    except Exception as e:
        logger.error(f"Error extracting broadcast info: {e}")
        return "sendall"

def extract_log_clear(action):
    """Извлекает информацию об очистке логов"""
    try:
        if "все логи" in action:
            return "clearlogs all"
        elif "администратора" in action:
            admin_id = action.split("админ:")[1].strip()
            return f"clearlogs {admin_id}"
        else:
            return "clearlogs"
    except Exception as e:
        logger.error(f"Error extracting log clear: {e}")
        return "clearlogs"

def get_admin_logs(admin_id=None, days=30):
    """Возвращает логи администраторов за указанный период"""
    try:
        if not os.path.exists(ADMIN_LOGFILE):
            logger.warning(f"Admin log file not found: {ADMIN_LOGFILE}")
            return []
        
        # Используем UTC время для сравнения
        cutoff_date = (datetime.datetime.utcnow() - datetime.timedelta(days=days))
        
        with open(ADMIN_LOGFILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        logs = []
        for line in lines:
            try:
                if not line.strip():
                    continue
                    
                # Парсим строку лога
                timestamp_str, content = parse_log_line(line.strip())
                if not timestamp_str or not content:
                    continue
                
                # Убираем миллисекунды если есть
                if ',' in timestamp_str:
                    timestamp_str = timestamp_str.split(',')[0]
                
                # Парсим время из лога
                try:
                    log_time = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                    
                    # Сравниваем время
                    if log_time >= cutoff_date:
                        if admin_id:
                            if f"ADMIN {admin_id}" in content or f" {admin_id} " in content:
                                logs.append(line.strip())
                        else:
                            logs.append(line.strip())
                except ValueError as e:
                    logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
                    # Все равно добавляем лог если не можем распарсить время
                    if not admin_id or f"ADMIN {admin_id}" in line or f" {admin_id} " in line:
                        logs.append(line.strip())
            
            except Exception as e:
                logger.error(f"Error parsing log line: {line} - {e}")
                continue
        
        logger.info(f"Found {len(logs)} admin logs for period {days} days")
        return logs
        
    except Exception as e:
        logger.exception("Failed to read admin logs: %s", e)
        return []

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
    def _restore():
        time.sleep(BUTTON_COOLDOWN)
        try:
            markup = ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(KeyboardButton("📞 Попросить связаться со мной."))
            bot.send_message(user_id, "✅ Кнопка запроса связи снова доступна!", reply_markup=markup)
        except Exception as e:
            logger.error(f"Failed to restore button for user {user_id}: {e}")
    
    Thread(target=_restore, daemon=True).start()

# ----------------------------
# Flask keep-alive
# ----------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Bot is alive and running! TG SEARCH: @KVZDR_BOT"

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
        logger.info(f"Initializing database with ADMIN_ID: {ADMIN_ID}")
        
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        
        # Создаем таблицы
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
                  (ADMIN_ID, "kvazador", "kvazador", True))
        
        # Проверяем что админ добавлен
        c.execute("SELECT * FROM admins WHERE user_id = ?", (ADMIN_ID,))
        admin_check = c.fetchone()
        if admin_check:
            logger.info(f"✅ Main admin successfully added: {admin_check}")
        else:
            logger.error(f"❌ Failed to add main admin: {ADMIN_ID}")
        
        # Показываем всех админов в логах
        c.execute("SELECT * FROM admins")
        all_admins = c.fetchall()
        logger.info(f"All admins in DB: {all_admins}")
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {DB_PATH}")
        
    except Exception as e:
        logger.exception(f"Failed to initialize DB: {e}")

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
        
        if ban_type == "temporary" and duration_seconds:
            banned_time = datetime.datetime.strptime(banned_at, '%Y-%m-%d %H:%M:%S')
            current_time = datetime.datetime.utcnow()
            time_passed = (current_time - banned_time).total_seconds()
            
            if time_passed >= duration_seconds:
                unban_user(user_id)
                return None
            else:
                time_left = duration_seconds - time_passed
                return {
                    'type': ban_type,
                    'time_left': time_left,
                    'reason': reason
                }
        
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
            return True
        
        last_request = datetime.datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
        current_time = datetime.datetime.utcnow()
        time_passed = (current_time - last_request).total_seconds()
        
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
            
            # Логируем старт пользователя
            log_user_action(message.from_user, "start")
            
        except Exception:
            logger.exception("Error in /start handler for message: %s", message)

    @bot.message_handler(commands=['help'])
    def help_command(message):
        """Показывает список доступных команд для текущего пользователя"""
        try:
            user_id = int(message.from_user.id)
            is_user_admin = is_admin(user_id)
            ban_info = is_banned(user_id)
            
            help_text = "Доступные команды:\n\n"
            
            help_text += "Основные:\n"
            help_text += "/start - Начать работу с ботом\n"
            help_text += "/help - Показать это сообщение\n\n"
            
            if ban_info and ban_info['type'] == 'permanent':
                help_text += "Для забаненных:\n"
                help_text += "/unban - Запросить разбан\n\n"
            
            if not ban_info:
                help_text += "Общение:\n"
                help_text += "Просто напиши сообщение - оно дойдет до kvazador\n"
                help_text += "Кнопка '📞 Попросить связаться' - для срочных вопросов\n\n"
            
            if is_user_admin:
                help_text += "Администратор:\n"
                help_text += "/stats - Статистика бота\n"
                help_text += "/getusers - Список пользователей\n"
                help_text += "/sendall - Рассылка сообщений\n"
                help_text += "/ban - Временный бан\n"
                help_text += "/spermban - Перманентный бан\n"
                help_text += "/obossat - Разбан\n"
                help_text += "/reply - Ответить пользователю\n"
                help_text += "/stop - Закончить ответ\n\n"
                
                if is_main_admin(user_id):
                    help_text += "Главный администратор:\n"
                    help_text += "/addadmin - Добавить админа\n"
                    help_text += "/removeadmin - Удалить админа\n"
                    help_text += "/admins - Список админов\n"
                    help_text += "/adminlogs - Логи админов\n"
                    help_text += "/clearlogs - Очистить логи\n"
                    help_text += "/logstats - Статистика логов\n\n"
            
            help_text += "Просто напиши сообщение чтобы связаться с kvazador!"
            
            bot.send_message(user_id, help_text)
            
        except Exception:
            logger.exception("Error in /help handler: %s", message)

    # ==================== КОМАНДЫ БАНОВ ====================

    @bot.message_handler(commands=['ban'])
    def ban_command(message):
        """Временный бан пользователя"""
        logger.info(f"🎯 /ban handler triggered by {message.from_user.id}")
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

            if is_admin(target_id):
                bot.send_message(user_id, "❌ Нельзя забанить администратора.")
                return

            if ban_user(target_id, "temporary", duration, reason, user_id):
                try:
                    duration_text = format_time_left(duration)
                    bot.send_message(target_id, f"🚫 Вы были забанены на {duration_text}.\nПричина: {reason}")
                except Exception as e:
                    logger.warning("Could not notify banned user %s: %s", target_id, e)

                target_username = "Неизвестно"
                target_first_name = "Неизвестно"
                try:
                    target_chat = bot.get_chat(target_id)
                    target_username = f"@{target_chat.username}" if target_chat.username else None
                    target_first_name = target_chat.first_name or "Неизвестно"
                except:
                    target_username = None
                    target_first_name = "Неизвестно"

                target_info = format_target_info(target_id, target_username, target_first_name)
                bot.send_message(user_id, f"✅ Пользователь {target_info} забанен на {format_time_left(duration)}.\nПричина: {reason}")
                
                # Логируем действие в новом формате
                log_admin_action(message.from_user, "ban", target_info, f"[{duration}сек] [{reason}]")
            else:
                bot.send_message(user_id, "❌ Ошибка при бане пользователя.")
                
        except Exception:
            logger.exception("Error in /ban handler: %s", message)

    @bot.message_handler(commands=['spermban'])
    def permanent_ban_command(message):
        """Перманентный бан пользователя"""
        logger.info(f"🎯 /spermban handler triggered by {message.from_user.id}")
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

            if is_admin(target_id):
                bot.send_message(user_id, "❌ Нельзя забанить администратора.")
                return

            if ban_user(target_id, "permanent", None, reason, user_id):
                try:
                    bot.send_message(target_id, f"🚫 Вы были забанены навсегда.\nПричина: {reason}\n\nДля запроса разбана используйте /unban")
                except Exception as e:
                    logger.warning("Could not notify banned user %s: %s", target_id, e)

                target_username = "Неизвестно"
                target_first_name = "Неизвестно"
                try:
                    target_chat = bot.get_chat(target_id)
                    target_username = f"@{target_chat.username}" if target_chat.username else None
                    target_first_name = target_chat.first_name or "Неизвестно"
                except:
                    target_username = None
                    target_first_name = "Неизвестно"

                target_info = format_target_info(target_id, target_username, target_first_name)
                bot.send_message(user_id, f"✅ Пользователь {target_info} забанен навсегда.\nПричина: {reason}")
                
                # Логируем действие в новом формате
                log_admin_action(message.from_user, "permban", target_info, f"[{reason}]")
            else:
                bot.send_message(user_id, "❌ Ошибка при бане пользователя.")
                
        except Exception:
            logger.exception("Error in /spermban handler: %s", message)

    @bot.message_handler(commands=['unban'])
    def unban_request_command(message):
        """Запрос разбана от пользователя (только для пермаченных)"""
        try:
            user_id = int(message.from_user.id)
            
            ban_info = is_banned(user_id)
            if not ban_info or ban_info['type'] != 'permanent':
                bot.send_message(user_id, "❌ Эта команда только для перманентно забаненных пользователей.")
                return

            if not can_request_unban(user_id):
                bot.send_message(user_id, "❌ Вы уже отправляли запрос на разбан. Следующая попытка будет доступна через неделю после последнего запроса.")
                return

            user_unban_mode[user_id] = True
            bot.send_message(user_id, "✍️ Напишите сообщение для модераторов, почему мы должны вас разбанить. Постарайтесь, ведь следующая попытка будет только через неделю.")
            
        except Exception:
            logger.exception("Error in /unban handler: %s", message)

    @bot.message_handler(commands=['obossat'])
    def unban_command(message):
        """Разбан пользователя администратором"""
        logger.info(f"🎯 /obossat handler triggered by {message.from_user.id}")
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

            ban_info = is_banned(target_id)
            if not ban_info:
                bot.send_message(user_id, f"ℹ️ Пользователь {target_id} не забанен.")
                return

            if unban_user(target_id):
                unban_message = "✅ Вы были разбанены. Больше не нарушайте правила!"
                if len(parts) > 2:
                    unban_message = ' '.join(parts[2:])
                
                try:
                    bot.send_message(target_id, unban_message)
                except Exception as e:
                    logger.warning("Could not notify unbanned user %s: %s", target_id, e)

                target_username = "Неизвестно"
                target_first_name = "Неизвестно"
                try:
                    target_chat = bot.get_chat(target_id)
                    target_username = f"@{target_chat.username}" if target_chat.username else None
                    target_first_name = target_chat.first_name or "Неизвестно"
                except:
                    target_username = None
                    target_first_name = "Неизвестно"

                target_info = format_target_info(target_id, target_username, target_first_name)
                bot.send_message(user_id, f"✅ Пользователь {target_info} разбанен.")
                
                # Логируем действие в новом формате
                log_admin_action(message.from_user, "obossat", target_info)
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

            update_unban_request_date(user_id)
            user_unban_mode[user_id] = False
            
            bot.send_message(user_id, "✅ Ваш запрос на разбан отправлен модераторам. Следующая попытка будет доступна через неделю.")
            
        except Exception:
            logger.exception("Error in unban request handler: %s", message)

    # ==================== КОМАНДЫ ДЛЯ УПРАВЛЕНИЯ ЛОГАМИ ====================

    @bot.message_handler(commands=['adminlogs'])
    def show_admin_logs(message):
        """Показывает логи администраторов (только для главного админа)"""
        logger.info(f"🎯 /adminlogs handler triggered by {message.from_user.id}")
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            parts = message.text.split()
            days = 30
            
            target_admin_id = None
            if len(parts) >= 2:
                try:
                    target_admin_id = int(parts[1])
                except ValueError:
                    if parts[1].lower() == 'all':
                        target_admin_id = None
                    else:
                        bot.send_message(user_id, "❌ Используй:\n/adminlogs - логи всех админов за месяц\n/adminlogs all - то же самое\n/adminlogs 123456789 - логи конкретного админа\n/adminlogs 123456789 7 - логи админа за 7 дней")
                        return
            
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

            formatted_logs = format_admin_logs_for_display(logs, days)
            
            # Разбиваем на части если слишком длинное сообщение
            if len(formatted_logs) > 4000:
                parts = [formatted_logs[i:i+4000] for i in range(0, len(formatted_logs), 4000)]
                for part in parts:
                    bot.send_message(user_id, part)
                    time.sleep(0.5)
            else:
                bot.send_message(user_id, formatted_logs)

            bot.send_message(user_id, f"📈 Всего записей: {len(logs)}")

            # Логируем просмотр логов
            action = f"adminlogs"
            target_info = f"{target_admin_id}" if target_admin_id else "all"
            additional_info = f"[{days} дней]"
            log_admin_action(message.from_user, action, target_info, additional_info)
            
        except Exception:
            logger.exception("Error in /adminlogs handler: %s", message)

    @bot.message_handler(commands=['clearlogs'])
    def clear_logs_command(message):
        """Очищает логи администраторов (только для главного админа)"""
        logger.info(f"🎯 /clearlogs handler triggered by {message.from_user.id}")
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
                try:
                    with open(ADMIN_LOGFILE, 'w', encoding='utf-8') as f:
                        f.write("")
                    bot.send_message(user_id, "✅ Все логи администраторов очищены.")
                    
                    # Логируем действие в новом формате
                    log_admin_action(message.from_user, "clearlogs", "all")
                    
                except Exception as e:
                    bot.send_message(user_id, f"❌ Ошибка при очистке логов: {e}")
                    logger.error(f"Failed to clear admin logs: {e}")
                
            else:
                try:
                    target_id = int(target)
                    logs = get_admin_logs(None, 36500)
                    
                    admin_username = None
                    try:
                        admin_chat = bot.get_chat(target_id)
                        admin_username = f"@{admin_chat.username}" if admin_chat.username else admin_chat.first_name
                    except:
                        pass
                    
                    filtered_logs = []
                    for log in logs:
                        if f"ADMIN {target_id}" in log:
                            continue
                        if admin_username and admin_username in log:
                            continue
                        filtered_logs.append(log)
                    
                    with open(ADMIN_LOGFILE, 'w', encoding='utf-8') as f:
                        for log in filtered_logs:
                            f.write(log + '\n')
                    
                    bot.send_message(user_id, f"✅ Логи администратора {target_id} очищены.")
                    
                    # Логируем действие в новом формате
                    log_admin_action(message.from_user, "clearlogs", f"{target_id}")
                    
                except ValueError:
                    bot.send_message(user_id, "❌ Неверный user_id. Используй число или 'all'")
                    
        except Exception:
            logger.exception("Error in /clearlogs handler: %s", message)

    @bot.message_handler(commands=['logstats'])
    def show_log_statistics(message):
        """Показывает статистику по логам администраторов"""
        logger.info(f"🎯 /logstats handler triggered by {message.from_user.id}")
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            parts = message.text.split()
            days = 30
            
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

            admin_actions = {}
            action_types = {}
            
            for log in logs:
                try:
                    # Извлекаем ID админа и действие из лога
                    if 'ADMIN' in log:
                        parts = log.split('ADMIN ', 1)
                        if len(parts) > 1:
                            admin_part = parts[1].split(')', 1)[0]
                            admin_id = admin_part.split(' (')[0]
                            
                            action_part = log.split(' - ', 1)[1] if ' - ' in log else log
                            
                            if admin_id not in admin_actions:
                                admin_actions[admin_id] = 0
                            admin_actions[admin_id] += 1
                            
                            # Определяем тип действия
                            action_type = "другое"
                            if 'бан' in action_part.lower():
                                action_type = "бан"
                            elif 'разбан' in action_part.lower():
                                action_type = "разбан"
                            elif 'рассылка' in action_part.lower():
                                action_type = "рассылка"
                            elif 'добавление администратора' in action_part.lower():
                                action_type = "добавление админа"
                            elif 'удаление администратора' in action_part.lower():
                                action_type = "удаление админа"
                            elif 'просмотр' in action_part.lower():
                                action_type = "просмотр"
                            elif 'очистка' in action_part.lower():
                                action_type = "очистка логов"
                                
                            if action_type not in action_types:
                                action_types[action_type] = 0
                            action_types[action_type] += 1
                except Exception as e:
                    logger.error(f"Error parsing log for stats: {log} - {e}")
                    continue

            stats_text = f"Статистика логов администраторов за {days} дней:\n\n"
            stats_text += f"Всего записей: {len(logs)}\n\n"
            
            if admin_actions:
                stats_text += "Активность по администраторам:\n"
                for admin_id, count in sorted(admin_actions.items(), key=lambda x: x[1], reverse=True):
                    admin_name = "Неизвестно"
                    try:
                        admin_chat = bot.get_chat(int(admin_id))
                        admin_name = f"@{admin_chat.username}" if admin_chat.username else admin_chat.first_name
                    except:
                        admin_name = f"ID: {admin_id}"
                    
                    stats_text += f"• {admin_name}: {count} действий\n"
            
            if action_types:
                stats_text += "\nТипы действий:\n"
                for action_type, count in sorted(action_types.items(), key=lambda x: x[1], reverse=True):
                    stats_text += f"• {action_type}: {count} раз\n"

            bot.send_message(user_id, stats_text)

            # Логируем просмотр статистики
            log_admin_action(message.from_user, "logstats", f"[{days} дней]")
            
        except Exception:
            logger.exception("Error in /logstats handler: %s", message)

    # ==================== СИСТЕМА АДМИНИСТРИРОВАНИЯ ====================

    @bot.message_handler(commands=['addadmin'])
    def add_admin_command(message):
        """Добавляет обычного админа (только для главного админа)"""
        logger.info(f"🎯 /addadmin handler triggered by {message.from_user.id}")
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

            if target_id == user_id:
                bot.send_message(user_id, "❌ Вы уже ГА.")
                return

            try:
                target_user = bot.get_chat(target_id)
                username = target_user.username
                first_name = target_user.first_name
            except Exception:
                username = None
                first_name = "Unknown"

            if add_admin(target_id, username, first_name):
                target_info = format_target_info(target_id, username, first_name)
                bot.send_message(user_id, f"✅ Пользователь {target_info} добавлен как администратор.")
                
                # Логируем действие в новом формате
                log_admin_action(message.from_user, "addadmin", target_info)
                
                try:
                    bot.send_message(target_id, "🎉 Вы были назначены администратором бота!\n\nТеперь вам доступны команды:\n/stats - статистика пользователей\n/getusers - список всех пользователей\n/sendall - рассылка сообщений\n/ban - временный бан\n/spermban - перманентный бан\n/obossat - разбан")
                except Exception:
                    logger.warning("Could not notify new admin %s", target_id)
            else:
                bot.send_message(user_id, "❌ Ошибка при добавлении администратора.")
                
        except Exception:
            logger.exception("Error in /addadmin handler: %s", message)

    @bot.message_handler(commands=['removeadmin'])
    def remove_admin_command(message):
        """Удаляет админа (только для главного админа)"""
        logger.info(f"🎯 /removeadmin handler triggered by {message.from_user.id}")
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

            if target_id == user_id:
                bot.send_message(user_id, "❌ Нельзя удалить главного администратора.")
                return

            if remove_admin(target_id):
                target_info = f"ID: {target_id}"
                try:
                    target_chat = bot.get_chat(target_id)
                    if target_chat.username:
                        target_info = f"@{target_chat.username} ({target_id})"
                    else:
                        target_info = f"{target_chat.first_name} ({target_id})"
                except:
                    pass
                    
                bot.send_message(user_id, f"✅ Администратор {target_info} удален.")
                
                # Логируем действие в новом формате
                log_admin_action(message.from_user, "removeadmin", target_info)
                
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
        logger.info(f"🎯 /admins handler triggered by {message.from_user.id}")
        try:
            user_id = int(message.from_user.id)
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для главного администратора.")
                return

            admins = get_all_admins()
            if not admins:
                bot.send_message(user_id, "📝 Список администраторов пуст.")
                return

            admin_list = "Список администраторов:\n\n"
            for admin in admins:
                admin_id, username, first_name, is_main_admin = admin
                role = "👑 Главный" if is_main_admin else "🔹 Обычный"
                admin_list += f"{role} админ: {first_name or 'No name'}"
                if username:
                    admin_list += f" (@{username})"
                admin_list += f" | ID: {admin_id}\n"

            bot.send_message(user_id, admin_list)
            
            # Логируем просмотр списка админов
            log_admin_action(message.from_user, "admins")
            
        except Exception:
            logger.exception("Error in /admins handler: %s", message)

    @bot.message_handler(commands=['stats'])
    def stats_command(message):
        """Показывает статистику пользователей (для всех админов)"""
        logger.info(f"🎯 /stats handler triggered by {message.from_user.id}")
        try:
            user_id = int(message.from_user.id)
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Эта команда только для администраторов.")
                return

            count = get_user_count()
            
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

            stats_text = f"Статистика бота:\n\nВсего пользователей: {count}\n"
            stats_text += f"Перманентно забанено: {permanent_bans}\n"
            stats_text += f"Временно забанено: {temporary_bans}"
            
            bot.send_message(user_id, stats_text)
            
            # Логируем просмотр статистики
            log_admin_action(message.from_user, "stats")
            
        except Exception:
            logger.exception("Error in /stats handler: %s", message)

    @bot.message_handler(commands=['getusers'])
    def get_users_command(message):
        """Показывает список всех пользователей (для всех админов)"""
        logger.info(f"🎯 /getusers handler triggered by {message.from_user.id}")
        try:
            admin_id = int(message.from_user.id)  # 👈 Меняем название переменной
            
            if not is_admin(admin_id):
                bot.send_message(admin_id, "❌ Эта команда только для администраторов.")
                return
    
            users = get_all_users()
            if not users:
                bot.send_message(admin_id, "📝 База пользователей пуста.")
                return
    
            user_list = "Список всех пользователей:\n\n"
            
            for user in users:
                user_id, username, first_name, last_name = user
                name = first_name or ""
                if last_name:
                    name += f" {last_name}"
                if not name.strip():
                    name = "No name"
                
                user_entry = f"🆔 {user_id} | {name}"
                if username:
                    user_entry += f" (@{username})"
                user_entry += "\n"
    
                # Если текущий список слишком длинный, отправляем его
                if len(user_list) + len(user_entry) > 4000:
                    bot.send_message(admin_id, user_list)  # 👈 Отправляем админу
                    user_list = "Список продолжение:\n\n"  # 👈 Начинаем новую часть
                
                user_list += user_entry
    
            # Отправляем оставшуюся часть
            if user_list:
                bot.send_message(admin_id, user_list)  # 👈 Отправляем админу
                
            # Логируем просмотр списка пользователей
            log_admin_action(message.from_user, "getusers")
                
        except Exception:
            logger.exception("Error in /getusers handler: %s", message)
        @bot.message_handler(commands=['sendall'])
        
    def send_all_command(message):
        """Рассылка сообщения всем пользователям (для всех админов)"""
        logger.info(f"🎯 /sendall handler triggered by {message.from_user.id}")
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
                    if is_banned(user[0]):
                        continue
                        
                    bot.send_message(user[0], f"{broadcast_text}")
                    success_count += 1
                    time.sleep(0.1)
                except Exception as e:
                    logger.error(f"Failed to send broadcast to {user[0]}: {e}")
                    fail_count += 1

            bot.send_message(user_id, f"✅ Рассылка завершена:\n\nУспешно: {success_count}\nНе удалось: {fail_count}\nПропущено (забанены): {len(users) - success_count - fail_count}")
            
            # Логируем рассылку
            log_admin_action(message.from_user, "sendall", f"[users: {len(users)}, success: {success_count}]")
            
        except Exception:
            logger.exception("Error in /sendall handler: %s", message)

    # ==================== ДИАГНОСТИЧЕСКИЕ КОМАНДЫ ====================

    @bot.message_handler(commands=['debug'])
    def debug_command(message):
        """Диагностическая команда"""
        try:
            user_id = int(message.from_user.id)
            
            debug_text = f"Диагностика:\n\n"
            debug_text += f"User ID: {user_id}\n"
            debug_text += f"Текст: {message.text}\n"
            debug_text += f"Время: {get_current_time()}\n\n"
            
            debug_text += f"Статистика обработчиков:\n"
            debug_text += f"• user_reply_mode: {user_id in user_reply_mode}\n"
            debug_text += f"• user_unban_mode: {user_id in user_unban_mode}\n"
            
            debug_text += f"Права:\n"
            debug_text += f"• Администратор: {is_admin(user_id)}\n"
            debug_text += f"• Главный администратор: {is_main_admin(user_id)}\n"
            
            ban_info = is_banned(user_id)
            debug_text += f"Бан: {ban_info if ban_info else 'Нет'}\n"
            
            # Проверка файлов логов
            debug_text += f"\nФайлы логов:\n"
            debug_text += f"• Основной лог: {os.path.exists(LOGFILE)}\n"
            debug_text += f"• Логи админов: {os.path.exists(ADMIN_LOGFILE)}\n"
            debug_text += f"• База данных: {os.path.exists(DB_PATH)}\n"
            
            bot.send_message(user_id, debug_text)
            
        except Exception as e:
            logger.exception(f"Error in /debug: {e}")

    @bot.message_handler(commands=['myrights'])
    def check_my_rights(message):
        """Проверяет права текущего пользователя"""
        try:
            user_id = int(message.from_user.id)
            
            rights_text = f"Ваши права:\n\n"
            rights_text += f"Ваш ID: {user_id}\n"
            rights_text += f"Имя: {message.from_user.first_name}\n"
            if message.from_user.username:
                rights_text += f"Username: @{message.from_user.username}\n"
            
            rights_text += f"\nПроверки:\n"
            rights_text += f"Администратор: {'✅ ДА' if is_admin(user_id) else '❌ НЕТ'}\n"
            rights_text += f"Главный администратор: {'✅ ДА' if is_main_admin(user_id) else '❌ НЕТ'}\n"
            
            ban_info = is_banned(user_id)
            if ban_info:
                rights_text += f"Забанен: ✅ ДА\n"
                rights_text += f"Тип бана: {ban_info['type']}\n"
                if 'time_left' in ban_info:
                    rights_text += f"Осталось: {format_time_left(ban_info['time_left'])}\n"
            else:
                rights_text += f"Забанен: ❌ НЕТ\n"
            
            bot.send_message(user_id, rights_text)
            
        except Exception as e:
            logger.exception(f"Error in /myrights: {e}")

    # ==================== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ====================

    @bot.message_handler(func=lambda message: message.text == "📞 Попросить связаться со мной.")
    def handle_contact_request(message):
        try:
            user_id = int(message.from_user.id)
            
            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда и не можете использовать эту функцию.")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены и не можете использовать эту функцию. До разбана осталось: {time_left}")
                return
            
            cooldown_remaining = check_button_cooldown(user_id)
            if cooldown_remaining > 0:
                bot.send_message(
                    user_id, 
                    f"⏳ Кнопка будет доступна через {int(cooldown_remaining)} секунд",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            bot.send_message(
                user_id, 
                "✅ Ваш запрос на связь отправлен. Ожидайте ответа.\n\n"
                f"🕒 Кнопка связи появится снова через {BUTTON_COOLDOWN} секунд",
                reply_markup=ReplyKeyboardRemove()
            )
            
            admin_text = f"📞 Пользователь {message.from_user.first_name} "
            admin_text += f"@{message.from_user.username or 'без username'} "
            admin_text += f"(ID: {user_id}) просит связаться."
            
            admins = get_all_admins()
            for admin in admins:
                try:
                    bot.send_message(admin[0], admin_text)
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin[0]}: {e}")
            
            # Логируем запрос связи
            log_user_action(message.from_user, "contact_request")
            
            restore_button(user_id)
            
        except Exception:
            logger.exception("Error in contact request handler: %s", message)

    @bot.message_handler(commands=['reply'])
    def start_reply_mode(message):
        logger.info(f"🎯 /reply handler triggered by {message.from_user.id}")
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
        logger.info(f"🎯 /stop handler triggered by {message.from_user.id}")
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

            if is_banned(target_user_id):
                bot.send_message(user_id, "❌ Нельзя отправить сообщение забаненному пользователю.")
                return

            try:
                bot.send_message(target_user_id, f"💌 Поступил ответ от kvazador:\n\n{message.text}")
                bot.send_message(user_id, f"✅ Ответ отправлен пользователю ID: {target_user_id}")
                
                # Логируем отправку ответа
                target_info = f"ID: {target_user_id}"
                try:
                    target_chat = bot.get_chat(target_user_id)
                    if target_chat.username:
                        target_info = f"@{target_chat.username} ({target_user_id})"
                    else:
                        target_info = f"{target_chat.first_name} ({target_user_id})"
                except:
                    pass
                    
                log_admin_action(message.from_user, "reply", target_info, f"[{message.text}]")
                
            except Exception as e:
                logger.exception("Failed to send admin reply to %s: %s", target_user_id, e)
                bot.send_message(user_id, f"❌ Ошибка отправки: {e}")
        except Exception:
            logger.exception("Error in admin reply handler: %s", message)

    # Обработчик неизвестных команд - ДОЛЖЕН БЫТЬ ПОСЛЕДНИМ
    @bot.message_handler(func=lambda message: message.text and message.text.startswith('/'))
    def unknown_command(message):
        """Обрабатывает неизвестные команды"""
        try:
            user_id = int(message.from_user.id)
            command = message.text.split()[0]
            
            known_commands = [
                '/start', '/help', '/ban', '/spermban', '/unban', '/obossat',
                '/addadmin', '/removeadmin', '/admins', '/stats', '/getusers',
                '/sendall', '/reply', '/stop', '/adminlogs', '/clearlogs', '/logstats',
                '/debug', '/myrights'
            ]
            
            if command not in known_commands:
                bot.send_message(
                    user_id, 
                    f"❌ Команда {command} не найдена.\n\n"
                    f"Используй /help чтобы увидеть все доступные команды."
                )
            else:
                logger.warning(f"Known command {command} was caught by unknown_command handler!")
                
        except Exception:
            logger.exception("Error in unknown command handler: %s", message)

    @bot.message_handler(content_types=['text'])
    def forward_text_message(message):
        try:
            user_id = int(message.from_user.id)

            if message.text.startswith('/'):
                return

            if message.text == "📞 Попросить связаться со мной.":
                return handle_contact_request(message)

            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда. Для разбана используйте /unban")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены. До разбана осталось: {time_left}")
                return

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
            user_info += f"\n⏰ {get_current_time()}"

            admins = get_all_admins()
            for admin in admins:
                try:
                    bot.send_message(admin[0], f"{user_info}\n\n📨 Сообщение:\n\n{message.text}")
                except Exception as e:
                    logger.error(f"Failed to forward message to admin {admin[0]}: {e}")

            bot.send_message(user_id, "✅ Сообщение отправлено kvazador!")
            
            # Логируем отправку сообщения пользователем
            if not is_admin(user_id):
                log_user_action(message.from_user, "message", f"[{message.text}]")
            
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

            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда и не можете отправлять медиа.")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены и не можете отправлять медиа. До разбана осталось: {time_left}")
                return

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
            user_info += f"\n⏰ {get_current_time()}"

            caption = f"{user_info}\n\n"
            if message.caption:
                caption += f"📝 Подпись: {message.caption}"

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
            
            # Логируем отправку медиа пользователем
            if not is_admin(user_id):
                media_type = "media"
                if message.photo:
                    media_type = "photo"
                elif message.voice:
                    media_type = "voice"
                elif message.video:
                    media_type = "video"
                elif message.document:
                    media_type = "document"
                elif message.audio:
                    media_type = "audio"
                    
                log_user_action(message.from_user, f"{media_type}_message")
            
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

            ban_info = is_banned(user_id)
            if ban_info:
                if ban_info['type'] == 'permanent':
                    bot.send_message(user_id, "🚫 Вы забанены навсегда и не можете отправлять контакты/локации.")
                else:
                    time_left = format_time_left(ban_info['time_left'])
                    bot.send_message(user_id, f"🚫 Вы забанены и не можете отправлять контакты/локации. До разбана осталось: {time_left}")
                return

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
            user_info += f"\n⏰ {get_current_time()}"

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
            
            # Логируем отправку контакта/локации
            if not is_admin(user_id):
                data_type = "contact" if message.contact else "location"
                log_user_action(message.from_user, f"{data_type}_send")
            
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

    # Создаем файлы логов при запуске
    ensure_log_files()
    init_db()

    try:
        logger.info("Attempting bot.get_me() to verify token...")
        me = bot.get_me()
        logger.info("Bot connected as: %s (id=%s)", me.username, me.id)
    except Exception as e:
        logger.exception("Failed to connect to Telegram. Check BOT_TOKEN. %s", e)
        return

    logger.info("Bot is ready to receive messages.")

    while True:
        try:
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
