#!/usr/bin/env python3
# coding: utf-8

import os
import time
import logging
import datetime
from threading import Thread
import psycopg2
from psycopg2.extras import RealDictCursor
from collections import defaultdict
import random
import urllib.parse as urlparse

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

# ----------------------------
# PostgreSQL подключение
# ----------------------------

def get_db_connection():
    """Создает соединение с PostgreSQL"""
    try:
        # На Render используем DATABASE_URL
        database_url = os.environ.get('DATABASE_URL')
        
        if database_url:
            # Парсим URL для Render
            parsed = urlparse.urlparse(database_url)
            conn = psycopg2.connect(
                database=parsed.path[1:],
                user=parsed.username,
                password=parsed.password,
                host=parsed.hostname,
                port=parsed.port,
                sslmode='require'
            )
        else:
            # Локальная разработка
            conn = psycopg2.connect(
                database="bot_db",
                user="postgres",
                password="password",
                host="localhost",
                port="5432"
            )
        
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        raise

def safe_db_execute(func, *args, **kwargs):
    """Безопасное выполнение операций с БД"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except psycopg2.OperationalError as e:
            if "could not connect" in str(e) and attempt < max_retries - 1:
                wait_time = 0.5 * (attempt + 1)
                logger.warning(f"DB connection failed, retry {attempt + 1} in {wait_time}s")
                time.sleep(wait_time)
                continue
            else:
                logger.error(f"DB error after {max_retries} retries: {e}")
                raise
        except Exception as e:
            logger.error(f"Unexpected DB error: {e}")
            raise

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
        user_name = format_admin_name(user)
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
            date_part = timestamp_str.split()[0]
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
    
    sorted_dates = sorted(grouped_logs.keys(), reverse=True)
    
    for date in sorted_dates:
        result += f"============={date}=============\n"
        
        day_logs = grouped_logs[date]
        day_logs.sort(key=lambda x: x[0])
        
        for i, (time_part, content) in enumerate(day_logs, 1):
            display_time = time_part
            if len(display_time) > 8:
                display_time = display_time[:8]
            
            formatted_content = format_log_content(content)
            
            result += f"{i}. {display_time} - {formatted_content}\n"
        
        result += "\n"
    
    return result

def format_log_content(content):
    """Форматирует содержание лога в нужный формат"""
    if "ADMIN" in content:
        content = content.replace("ADMIN ", "")
        
        if " - " in content:
            admin_part, action_part = content.split(" - ", 1)
            
            if "(" in admin_part and ")" in admin_part:
                admin_id = admin_part.split(" ")[0]
                admin_name = admin_part.split("(")[1].split(")")[0]
            else:
                admin_name = admin_part
                
            formatted_action = format_admin_action(action_part)
            return f"{admin_name} {formatted_action}"
    
    return content

def format_admin_action(action):
    """Форматирует действие администратора"""
    action_lower = action.lower()
    
    if "временный бан" in action_lower:
        return extract_ban_info(action, "ban")
    elif "перманентный бан" in action_lower:
        return extract_ban_info(action, "permban")
    elif "разбан" in action_lower or "obossat" in action_lower:
        return extract_simple_action(action, "obossat")
    elif "отправка ответа пользователю" in action_lower or "ответ" in action_lower:
        return extract_reply_info(action)
    elif "добавление администратора" in action_lower:
        return extract_admin_management(action, "addadmin")
    elif "удаление администратора" in action_lower:
        return extract_admin_management(action, "removeadmin")
    elif "просмотр логов" in action_lower:
        return extract_log_view(action)
    elif "просмотр статистики" in action_lower:
        return "logstats"
    elif "просмотр списка" in action_lower:
        if "пользователей" in action_lower:
            return "getusers"
        elif "администраторов" in action_lower:
            return "admins"
    elif "рассылка" in action_lower:
        return extract_broadcast_info(action)
    elif "очистка" in action_lower:
        return extract_log_clear(action)
    
    return action

def extract_ban_info(action, ban_type):
    """Извлекает информацию о бане"""
    try:
        user_part = None
        if "пользователь:" in action:
            user_part = action.split("пользователь:")[1].split(",")[0].strip()
        elif "user:" in action:
            user_part = action.split("user:")[1].split(",")[0].strip()
        
        time_part = ""
        if ban_type == "ban" and "время:" in action:
            time_part = action.split("время:")[1].split(",")[0].strip()
            if "сек" in time_part:
                time_part = time_part.replace("сек", "сек")
        
        reason_part = ""
        if "причина:" in action:
            reason_part = action.split("причина:")[1].strip()
        elif "reason:" in action:
            reason_part = action.split("reason:")[1].strip()
        
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
            if "@@" in user_part:
                user_part = user_part.replace("@@", "@")
            return f"{action_type} {user_part}"
        elif "user:" in action:
            user_part = action.split("user:")[1].strip()
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
            if "@@" in admin_part:
                admin_part = admin_part.replace("@@", "@")
            return f"{action_type} {admin_part}"
        elif "new admin:" in action:
            admin_part = action.split("new admin:")[1].strip()
            if "@@" in admin_part:
                admin_part = admin_part.replace("@@", "@")
            return f"{action_type} {admin_part}"
        elif "удален админ:" in action:
            admin_part = action.split("удален админ:")[1].strip()
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
        
        cutoff_date = (datetime.datetime.utcnow() - datetime.timedelta(days=days))
        
        with open(ADMIN_LOGFILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        logs = []
        for line in lines:
            try:
                if not line.strip():
                    continue
                    
                timestamp_str, content = parse_log_line(line.strip())
                if not timestamp_str or not content:
                    continue
                
                if ',' in timestamp_str:
                    timestamp_str = timestamp_str.split(',')[0]
                
                try:
                    log_time = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                    
                    if log_time >= cutoff_date:
                        if admin_id:
                            if f"ADMIN {admin_id}" in content or f" {admin_id} " in content:
                                logs.append(line.strip())
                        else:
                            logs.append(line.strip())
                except ValueError as e:
                    logger.warning(f"Failed to parse timestamp '{timestamp_str}': {e}")
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

def init_db():
    """Создаёт таблицы, если их нет."""
    try:
        logger.info(f"Initializing database with ADMIN_ID: {ADMIN_ID}")
        
        def _init():
            conn = get_db_connection()
            c = conn.cursor()
            
            # Создаем таблицы
            c.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY, 
                    username TEXT, 
                    first_name TEXT, 
                    last_name TEXT, 
                    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY, 
                    username TEXT, 
                    first_name TEXT, 
                    is_main_admin BOOLEAN DEFAULT FALSE, 
                    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS bans (
                    user_id BIGINT PRIMARY KEY, 
                    ban_type TEXT NOT NULL,
                    ban_duration_seconds INTEGER,
                    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ban_reason TEXT,
                    banned_by BIGINT,
                    unban_request_date TIMESTAMP
                )
            ''')
            
            c.execute('''
                CREATE TABLE IF NOT EXISTS user_balance (
                    user_id BIGINT PRIMARY KEY, 
                    balance INTEGER DEFAULT 0
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS promocodes (
                    promocode TEXT PRIMARY KEY, 
                    value INTEGER, 
                    used BOOLEAN DEFAULT FALSE, 
                    used_by BIGINT
                )
            ''')
            
            # Добавляем главного админа если его нет
            c.execute("""
                INSERT INTO admins (user_id, username, first_name, is_main_admin) 
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO NOTHING
            """, (ADMIN_ID, "kvazador", "kvazador", True))
            
            # Проверяем что админ добавлен
            c.execute("SELECT * FROM admins WHERE user_id = %s", (ADMIN_ID,))
            admin_check = c.fetchone()
            if admin_check:
                logger.info(f"✅ Main admin successfully added: {admin_check}")
            else:
                logger.error(f"❌ Failed to add main admin: {ADMIN_ID}")
            
            c.execute("SELECT * FROM admins")
            all_admins = c.fetchall()
            logger.info(f"All admins in DB: {all_admins}")
            
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
        
        safe_db_execute(_init)
        
    except Exception as e:
        logger.exception(f"Failed to initialize DB: {e}")

def register_user(user_id, username, first_name, last_name):
    """Сохраняет/обновляет пользователя в БД."""
    def _register():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO users (user_id, username, first_name, last_name) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name
        """, (user_id, username, first_name, last_name))
        c.execute("""
            INSERT INTO user_balance (user_id, balance) 
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id, 0))
        conn.commit()
        conn.close()
        logger.debug("Registered user %s (%s)", user_id, username)
    
    safe_db_execute(_register)

def is_admin(user_id):
    """Проверяет, является ли пользователь админом"""
    def _check():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        conn.close()
        return result is not None
    
    return safe_db_execute(_check)

def is_main_admin(user_id):
    """Проверяет, является ли пользователь главным админом"""
    def _check():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins WHERE user_id = %s AND is_main_admin = TRUE", (user_id,))
        result = c.fetchone()
        conn.close()
        return result is not None
    
    return safe_db_execute(_check)

def add_admin(user_id, username, first_name):
    """Добавляет обычного админа"""
    def _add():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO admins (user_id, username, first_name) 
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name
        """, (user_id, username, first_name))
        conn.commit()
        conn.close()
        logger.info("Added admin %s (%s)", user_id, username)
        return True
    
    return safe_db_execute(_add)

def remove_admin(user_id):
    """Удаляет админа (кроме главного)"""
    def _remove():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM admins WHERE user_id = %s AND is_main_admin = FALSE", (user_id,))
        conn.commit()
        conn.close()
        logger.info("Removed admin %s", user_id)
        return True
    
    return safe_db_execute(_remove)

def get_all_users():
    """Возвращает список всех пользователей"""
    def _get_users():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id, username, first_name, last_name FROM users")
        users = c.fetchall()
        conn.close()
        return users
    
    return safe_db_execute(_get_users)

def get_user_count():
    """Возвращает количество пользователей"""
    def _get_count():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        count = c.fetchone()[0]
        conn.close()
        return count
    
    return safe_db_execute(_get_count)

def get_all_admins():
    """Возвращает список всех админов"""
    def _get_admins():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id, username, first_name, is_main_admin FROM admins")
        admins = c.fetchall()
        conn.close()
        return admins
    
    return safe_db_execute(_get_admins)

# ==================== СИСТЕМА БАНОВ ====================

def ban_user(user_id, ban_type, duration_seconds=None, reason="", banned_by=None):
    """Банит пользователя"""
    def _ban():
        conn = get_db_connection()
        c = conn.cursor()
        
        if ban_type == "permanent":
            c.execute('''
                INSERT INTO bans (user_id, ban_type, ban_duration_seconds, ban_reason, banned_by) 
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    ban_type = EXCLUDED.ban_type,
                    ban_duration_seconds = EXCLUDED.ban_duration_seconds,
                    ban_reason = EXCLUDED.ban_reason,
                    banned_by = EXCLUDED.banned_by,
                    banned_at = CURRENT_TIMESTAMP
            ''', (user_id, ban_type, None, reason, banned_by))
        else:
            c.execute('''
                INSERT INTO bans (user_id, ban_type, ban_duration_seconds, ban_reason, banned_by) 
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    ban_type = EXCLUDED.ban_type,
                    ban_duration_seconds = EXCLUDED.ban_duration_seconds,
                    ban_reason = EXCLUDED.ban_reason,
                    banned_by = EXCLUDED.banned_by,
                    banned_at = CURRENT_TIMESTAMP
            ''', (user_id, ban_type, duration_seconds, reason, banned_by))
        
        conn.commit()
        conn.close()
        logger.info("Banned user %s: type=%s, duration=%s", user_id, ban_type, duration_seconds)
        return True
    
    return safe_db_execute(_ban)

def unban_user(user_id):
    """Разбанивает пользователя"""
    def _unban():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM bans WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
        logger.info("Unbanned user %s", user_id)
        return True
    
    return safe_db_execute(_unban)

def is_banned(user_id):
    """Проверяет, забанен ли пользователь и возвращает информацию о бане"""
    def _check_ban():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT ban_type, ban_duration_seconds, banned_at, ban_reason FROM bans WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            return None
        
        ban_type, duration_seconds, banned_at, reason = result
        
        if ban_type == "temporary" and duration_seconds:
            # PostgreSQL возвращает datetime объект напрямую
            time_passed = (datetime.datetime.utcnow() - banned_at).total_seconds()
            
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
    
    return safe_db_execute(_check_ban)

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
    def _check():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT unban_request_date FROM bans WHERE user_id = %s AND ban_type = 'permanent'", (user_id,))
        result = c.fetchone()
        conn.close()
        
        if not result or not result[0]:
            return True
        
        last_request = result[0]
        current_time = datetime.datetime.utcnow()
        time_passed = (current_time - last_request).total_seconds()
        
        return time_passed >= 7 * 24 * 3600
    
    return safe_db_execute(_check)

def update_unban_request_date(user_id):
    """Обновляет дату последнего запроса на разбан"""
    def _update():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE bans SET unban_request_date = CURRENT_TIMESTAMP WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
        return True
    
    return safe_db_execute(_update)

# ==================== СИСТЕМА БУРМАЛДЫ И ПРОМОКОДОВ ====================

def get_user_balance(user_id):
    """Возвращает баланс пользователя"""
    def _get_balance():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT balance FROM user_balance WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else 0
    
    return safe_db_execute(_get_balance)

def update_user_balance(user_id, new_balance):
    """Обновляет баланс пользователя"""
    def _update():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE user_balance SET balance = %s WHERE user_id = %s", (new_balance, user_id))
        conn.commit()
        conn.close()
        return True
    
    return safe_db_execute(_update)

def add_promocode(promocode, value):
    """Добавляет промокод"""
    def _add():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO promocodes (promocode, value) 
            VALUES (%s, %s)
            ON CONFLICT (promocode) 
            DO UPDATE SET value = EXCLUDED.value
        """, (promocode, value))
        conn.commit()
        conn.close()
        logger.info("Added promocode: %s with value: %s", promocode, value)
        return True
    
    return safe_db_execute(_add)

def use_promocode(promocode, user_id):
    """Активирует промокод для пользователя"""
    def _use():
        conn = get_db_connection()
        c = conn.cursor()
        
        # Проверяем существует ли промокод и не использован ли он
        c.execute("SELECT value, used FROM promocodes WHERE promocode = %s", (promocode,))
        result = c.fetchone()
        
        if not result:
            return None, "Промокод не найден"
        
        value, used = result
        if used:
            return None, "Промокод уже использован"
        
        # Активируем промокод
        c.execute("UPDATE promocodes SET used = TRUE, used_by = %s WHERE promocode = %s", (user_id, promocode))
        
        # Обновляем баланс пользователя
        current_balance = get_user_balance(user_id)
        new_balance = current_balance + value
        success = update_user_balance(user_id, new_balance)
        
        if not success:
            return None, "Ошибка при обновлении баланса"
            
        conn.commit()
        conn.close()
        
        logger.info("User %s used promocode %s, got %s coins, new balance: %s", user_id, promocode, value, new_balance)
        return value, f"Промокод активирован! Вы получили {value} монет."
    
    return safe_db_execute(_use)

def get_promocode_stats():
    """Возвращает статистику по промокодам"""
    def _get_stats():
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) FROM promocodes")
        total = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM promocodes WHERE used = TRUE")
        used = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM promocodes WHERE used = FALSE")
        available = c.fetchone()[0]
        
        conn.close()
        
        return {
            'total': total,
            'used': used,
            'available': available
        }
    
    return safe_db_execute(_get_stats)

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
                "🎰 Добро пожаловать в KVZDR HUB! 🎰\n\n"
                "Это бот-пересыльщик сообщений для kvazador!\n\n"
                "Виртуальная бурмалда:\n"
                "Ваш текущий баланс: 0 монет\n"
                "Пополнить баланс можно через промокоды\n"
                "Для запроса промокода используйте /get_promo\n"
                "Для запуска казино используйте /casino\n\n"
                "📨 Связь с kvazador:\n"
                "Для связи просто отправьте сообщение здесь. "
                "Ответ может поступить через бота или в ЛС.\n\n"
                "🎁 Пополнение баланса:\n"
                "Запросите промокод через /get_promo и подождите пока его создаст модератор.\n"
                "Активируйте его через /promo ПРОМОКОД\n"
                "Каждый полученный промокод можно использовать только 1 раз!\n\n"
            )

            markup = ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(KeyboardButton("📞 Попросить связаться со мной."))
            markup.add(KeyboardButton("🎰 Запустить бурмалду"))
            markup.add(KeyboardButton("🎁 Запросить промокод"))
            bot.send_message(user_id, welcome_text, reply_markup=markup)
            
            log_user_action(message.from_user, "start")
            
        except Exception:
            logger.exception("Error in /start handler for message: %s", message)

    # ... остальные хэндлеры остаются такими же как в предыдущей версии
    # Просто замени все вызовы SQLite функций на PostgreSQL версии

# ----------------------------
# Основной цикл запуска бота
# ----------------------------

def start_bot_loop():
    """Запускает бота и перезапускает при ошибках."""
    if not bot:
        logger.error("Bot object is not created because BOT_TOKEN is missing.")
        return

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

# Webhook версия для Render
if os.environ.get('RENDER'):
    @app.route('/webhook', methods=['POST'])
    def webhook():
        if request.headers.get('content-type') == 'application/json':
            json_string = request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_string)
            bot.process_new_updates([update])
            return ''
        else:
            return 'Invalid content type', 400
    
    # ЯДЕРНЫЙ УДАР
    bot.remove_webhook()
    time.sleep(2)
    bot.set_webhook(url=f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook")
    
    logger.info("🤖 Webhook configured - bot is ready!")
    
    # Flask просто крутится, бот работает через webhook
    if __name__ == "__main__":
        keep_alive()
        
else:
    # Локально используем polling
    if __name__ == "__main__":
        keep_alive()
        try:
            bot.infinity_polling()
        except Exception as e:
            logger.exception("Polling error: %s", e)
