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

# ИМПОРТИРУЕМ request
from flask import Flask, request
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton

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
# ХЭНДЛЕРЫ БОТА - ВСЕ КОМАНДЫ
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
            
        except Exception as e:
            logger.exception("Error in /start handler for message: %s", message)

    @bot.message_handler(commands=['help'])
    def send_help(message):
        try:
            user_id = message.from_user.id
            is_user_admin = is_admin(user_id)
            
            if is_user_admin:
                help_text = (
                    "Доступные команды:\n\n"
                    "Для пользователей:\n"
                    "/start - начать работу\n"
                    "/help - эта справка\n"
                    "/casino - играть в казино\n"
                    "/balance - проверить баланс\n"
                    "/promo [код] - активировать промокод\n"
                    "/get_promo - запросить промокод\n"
                    "/unban - запросить разбан\n\n"
                    "Для админов:\n"
                    "/admin - админ панель\n"
                    "/add_promo [код] [сумма] - создать промокод\n"
                    "/adminlogs [дни] - логи админов\n"
                    "/restart - перезапуск бота\n"
                    "/debug - отладка\n"
                    "/myrights - мои права\n"
                    "/sendall [сообщение] - рассылка\n"
                    "/ban [id] [время] [причина] - бан\n"
                    "/unban [id] - разбан\n"
                    "/addadmin [id] - добавить админа\n"
                    "/removeadmin [id] - удалить админа\n"
                    "/users - список пользователей\n"
                    "/admins - список админов\n"
                    "/stats - статистика\n"
                    "/clearlogs - очистить логи"
                )
            else:
                help_text = (
                    "Доступные команды:\n\n"
                    "/start - начать работу\n"
                    "/help - эта справка\n"
                    "/casino - играть в казино\n"
                    "/balance - проверить баланс\n"
                    "/promo [код] - активировать промокод\n"
                    "/get_promo - запросить промокод\n"
                    "/unban - запросить разбан"
                )
            
            bot.send_message(user_id, help_text)
            log_user_action(message.from_user, "help")
            
        except Exception as e:
            logger.error(f"Error in /help: {e}")

    @bot.message_handler(commands=['balance'])
    def check_balance(message):
        try:
            user_id = message.from_user.id
            
            ban_info = is_banned(user_id)
            if ban_info:
                bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту команду")
                return
                
            balance = get_user_balance(user_id)
            bot.send_message(user_id, f"💰 Ваш текущий баланс: {balance} монет")
            log_user_action(message.from_user, "check_balance")
            
        except Exception as e:
            logger.error(f"Error in /balance: {e}")

    @bot.message_handler(commands=['casino'])
    def play_casino(message):
        try:
            user_id = message.from_user.id
            
            ban_info = is_banned(user_id)
            if ban_info:
                bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту команду")
                return
                
            balance = get_user_balance(user_id)
            if balance < 10:
                bot.send_message(user_id, "❌ Для игры в казино нужно минимум 10 монет")
                return
                
            # Простая логика казино
            win = random.choice([True, False, False])  # 33% шанс выигрыша
            if win:
                win_amount = random.randint(5, 50)
                new_balance = balance + win_amount
                update_user_balance(user_id, new_balance)
                bot.send_message(user_id, f"🎉 Поздравляем! Вы выиграли {win_amount} монет!\n💰 Новый баланс: {new_balance}")
            else:
                bet = 10
                new_balance = balance - bet
                update_user_balance(user_id, new_balance)
                bot.send_message(user_id, f"😞 Вы проиграли {bet} монет\n💰 Новый баланс: {new_balance}")
                
            log_user_action(message.from_user, "play_casino")
            
        except Exception as e:
            logger.error(f"Error in /casino: {e}")

    @bot.message_handler(commands=['promo'])
    def use_promo(message):
        try:
            user_id = message.from_user.id
            
            ban_info = is_banned(user_id)
            if ban_info:
                bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту команду")
                return
                
            args = message.text.split()
            if len(args) < 2:
                bot.send_message(user_id, "❌ Использование: /promo [код]")
                return
                
            promocode = args[1]
            value, result_message = use_promocode(promocode, user_id)
            
            if value is not None:
                bot.send_message(user_id, result_message)
                log_user_action(message.from_user, f"used_promo {promocode}")
            else:
                bot.send_message(user_id, f"❌ {result_message}")
                
        except Exception as e:
            logger.error(f"Error in /promo: {e}")

    @bot.message_handler(commands=['get_promo'])
    def request_promo(message):
        try:
            user_id = message.from_user.id
            
            ban_info = is_banned(user_id)
            if ban_info:
                bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту команду")
                return
                
            # Отправляем запрос всем админам
            admins = get_all_admins()
            user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            
            for admin in admins:
                try:
                    admin_id = admin[0]
                    bot.send_message(admin_id, f"🎫 Пользователь {user_info} (ID: {user_id}) запросил промокод")
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin[0]} about promo request: {e}")
                    
            bot.send_message(user_id, "✅ Ваш запрос на промокод отправлен администраторам. Ожидайте создания промокода.")
            log_user_action(message.from_user, "request_promo")
            
        except Exception as e:
            logger.error(f"Error in /get_promo: {e}")

    @bot.message_handler(commands=['unban'])
    def request_unban(message):
        try:
            user_id = message.from_user.id
            
            ban_info = is_banned(user_id)
            if not ban_info:
                bot.send_message(user_id, "✅ Вы не забанены")
                return
                
            if ban_info['type'] != 'permanent':
                time_left = format_time_left(ban_info['time_left'])
                bot.send_message(user_id, f"⏳ Вы временно забанены. До разбана осталось: {time_left}")
                return
                
            if not can_request_unban(user_id):
                bot.send_message(user_id, "❌ Вы можете запрашивать разбан только раз в неделю")
                return
                
            # Отправляем запрос админам
            admins = get_all_admins()
            user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            
            for admin in admins:
                try:
                    admin_id = admin[0]
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton("✅ Разбанить", callback_data=f"unban_{user_id}"))
                    bot.send_message(admin_id, 
                                   f"🔓 Пользователь {user_info} (ID: {user_id}) запросил разбан\n"
                                   f"Причина бана: {ban_info.get('reason', 'Не указана')}",
                                   reply_markup=markup)
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin[0]} about unban request: {e}")
                    
            update_unban_request_date(user_id)
            bot.send_message(user_id, "✅ Ваш запрос на разбан отправлен администраторам. Ожидайте решения.")
            log_user_action(message.from_user, "request_unban")
            
        except Exception as e:
            logger.error(f"Error in /unban: {e}")

    # ==================== АДМИН КОМАНДЫ ====================

    @bot.message_handler(commands=['admin'])
    def admin_panel(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            admin_text = (
                "Админ панель\n\n"
                "Управление пользователями:\n"
                "/ban [id] [время] [причина] - бан\n"
                "/unban [id] - разбан\n"
                "/users - список пользователей\n\n"
                "Промокоды:\n"
                "/add_promo [код] [сумма] - создать промокод\n"
                "/stats - статистика промокодов\n\n"
                "Логи и статистика:\n"
                "/adminlogs [дни] - логи админов\n"
                "/clearlogs - очистить логи\n\n"
                "Управление админами:\n"
                "/addadmin [id] - добавить админа\n"
                "/removeadmin [id] - удалить админа\n"
                "/admins - список админов\n"
                "/myrights - мои права\n\n"
                "Система:\n"
                "/sendall [сообщение] - рассылка\n"
                "/restart - перезапуск\n"
                "/debug - отладка"
            )
            bot.send_message(user_id, admin_text)
            log_admin_action(message.from_user, "открыл админ панель")
            
        except Exception as e:
            logger.error(f"Error in /admin: {e}")

    @bot.message_handler(commands=['add_promo'])
    def add_promo_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            args = message.text.split()[1:]
            if len(args) < 2:
                bot.send_message(user_id, "❌ Использование: /add_promo [код] [сумма]")
                return
                
            promocode = args[0]
            try:
                value = int(args[1])
            except ValueError:
                bot.send_message(user_id, "❌ Сумма должна быть числом")
                return
                
            if value <= 0:
                bot.send_message(user_id, "❌ Сумма должна быть положительной")
                return
                
            if add_promocode(promocode, value):
                bot.send_message(user_id, f"✅ Промокод {promocode} на {value} монет создан!")
                log_admin_action(message.from_user, f"создал промокод {promocode} на {value} монет")
            else:
                bot.send_message(user_id, "❌ Ошибка при создании промокода")
                
        except Exception as e:
            logger.error(f"Error in /add_promo: {e}")

    @bot.message_handler(commands=['adminlogs'])
    def admin_logs_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            args = message.text.split()[1:]
            days = 7
            if args:
                try:
                    days = int(args[0])
                    if days <= 0 or days > 365:
                        bot.send_message(user_id, "❌ Диапазон дней: 1-365")
                        return
                except ValueError:
                    bot.send_message(user_id, "❌ Количество дней должно быть числом")
                    return
            
            logs = get_admin_logs(days=days)
            if not logs:
                bot.send_message(user_id, f"📊 Логов за {days} дней не найдено")
                return
                
            formatted_logs = format_admin_logs_for_display(logs, days=days)
            
            if len(formatted_logs) > 4000:
                parts = [formatted_logs[i:i+4000] for i in range(0, len(formatted_logs), 4000)]
                for part in parts[:3]:
                    bot.send_message(user_id, f"```\n{part}\n```", parse_mode='Markdown')
                if len(parts) > 3:
                    bot.send_message(user_id, f"... и еще {len(parts)-3} частей")
            else:
                bot.send_message(user_id, f"```\n{formatted_logs}\n```", parse_mode='Markdown')
                
            log_admin_action(message.from_user, f"просмотрел логи за {days} дней")
            
        except Exception as e:
            logger.error(f"Error in /adminlogs: {e}")

    @bot.message_handler(commands=['restart'])
    def restart_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            bot.send_message(user_id, "🔄 Перезапуск бота...")
            logger.info(f"Admin {user_id} initiated restart")
            log_admin_action(message.from_user, "перезапустил бота")
            
            import os
            import sys
            os.execv(sys.executable, [sys.executable] + sys.argv)
            
        except Exception as e:
            logger.error(f"Error in /restart: {e}")

    @bot.message_handler(commands=['debug'])
    def debug_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            user_count = get_user_count()
            admins = get_all_admins()
            stats = get_promocode_stats()
            
            debug_info = (
                "Информация о системе:\n\n"
                f"Пользователей: {user_count}\n"
                f"Админов: {len(admins)}\n"
                f"Промокодов: {stats['total']} (использовано: {stats['used']}, доступно: {stats['available']})\n"
                f"Время: {get_current_time()}\n"
                f"Режим: {'WEBHOOK' if os.environ.get('RENDER') else 'POLLING'}"
            )
            bot.send_message(user_id, debug_info)
            log_admin_action(message.from_user, "запросил отладочную информацию")
            
        except Exception as e:
            logger.error(f"Error in /debug: {e}")

    @bot.message_handler(commands=['myrights'])
    def my_rights_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ Вы не являетесь администратором")
                return
                
            is_main = is_main_admin(user_id)
            rights_text = "👑 Вы главный администратор" if is_main else "⚡ Вы администратор"
            bot.send_message(user_id, rights_text)
            log_admin_action(message.from_user, "проверил свои права")
            
        except Exception as e:
            logger.error(f"Error in /myrights: {e}")

    @bot.message_handler(commands=['sendall'])
    def broadcast_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            args = message.text.split()[1:]
            if not args:
                bot.send_message(user_id, "❌ Использование: /sendall [сообщение]")
                return
                
            broadcast_text = ' '.join(args)
            users = get_all_users()
            success_count = 0
            total_count = len(users)
            
            bot.send_message(user_id, f"📢 Начинаю рассылку для {total_count} пользователей...")
            
            for user in users:
                try:
                    user_id_to_send = user[0]
                    bot.send_message(user_id_to_send, broadcast_text)
                    success_count += 1
                    time.sleep(0.1)  # Задержка чтобы не превысить лимиты
                except Exception as e:
                    logger.error(f"Failed to send broadcast to {user[0]}: {e}")
                    
            bot.send_message(user_id, f"✅ Рассылка завершена\nУспешно: {success_count}/{total_count}")
            log_admin_action(message.from_user, f"сделал рассылку: {broadcast_text[:50]}...")
            
        except Exception as e:
            logger.error(f"Error in /sendall: {e}")

    @bot.message_handler(commands=['ban'])
    def ban_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            args = message.text.split()[1:]
            if len(args) < 1:
                bot.send_message(user_id, "❌ Использование: /ban [id] [время_в_секундах] [причина]")
                return
                
            target_id = int(args[0])
            duration = None
            reason = "Не указана"
            
            if len(args) >= 2:
                try:
                    duration = int(args[1])
                    if duration <= 0:
                        bot.send_message(user_id, "❌ Время должно быть положительным числом")
                        return
                except ValueError:
                    bot.send_message(user_id, "❌ Время должно быть числом")
                    return
                    
            if len(args) >= 3:
                reason = ' '.join(args[2:])
                
            ban_type = "temporary" if duration else "permanent"
            
            if ban_user(target_id, ban_type, duration, reason, user_id):
                if duration:
                    time_str = format_time_left(duration)
                    bot.send_message(user_id, f"✅ Пользователь {target_id} забанен на {time_str}\nПричина: {reason}")
                else:
                    bot.send_message(user_id, f"✅ Пользователь {target_id} забанен навсегда\nПричина: {reason}")
                    
                try:
                    if duration:
                        bot.send_message(target_id, f"🚫 Вы забанены на {time_str}\nПричина: {reason}")
                    else:
                        bot.send_message(target_id, f"🚫 Вы забанены навсегда\nПричина: {reason}")
                except:
                    pass
                    
                log_admin_action(message.from_user, f"забанил пользователя {target_id}", f"время: {duration} сек, причина: {reason}")
            else:
                bot.send_message(user_id, "❌ Ошибка при бане пользователя")
                
        except Exception as e:
            logger.error(f"Error in /ban: {e}")

    @bot.message_handler(commands=['unban'])
    def unban_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            args = message.text.split()[1:]
            if len(args) < 1:
                bot.send_message(user_id, "❌ Использование: /unban [id]")
                return
                
            target_id = int(args[0])
            
            if unban_user(target_id):
                bot.send_message(user_id, f"✅ Пользователь {target_id} разбанен")
                
                try:
                    bot.send_message(target_id, "✅ Вы были разбанены")
                except:
                    pass
                    
                log_admin_action(message.from_user, f"разбанил пользователя {target_id}")
            else:
                bot.send_message(user_id, "❌ Ошибка при разбане пользователя")
                
        except Exception as e:
            logger.error(f"Error in /unban: {e}")

    @bot.message_handler(commands=['addadmin'])
    def add_admin_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Только главный администратор может добавлять админов")
                return
                
            args = message.text.split()[1:]
            if len(args) < 1:
                bot.send_message(user_id, "❌ Использование: /addadmin [id]")
                return
                
            target_id = int(args[0])
            
            # Получаем информацию о пользователе
            try:
                target_user = bot.get_chat(target_id)
                if add_admin(target_id, target_user.username, target_user.first_name):
                    bot.send_message(user_id, f"✅ Пользователь {target_user.first_name} ({target_id}) добавлен как администратор")
                    log_admin_action(message.from_user, f"добавил администратора {target_id}")
                else:
                    bot.send_message(user_id, "❌ Ошибка при добавлении администратора")
            except Exception as e:
                bot.send_message(user_id, f"❌ Не удалось найти пользователя с ID {target_id}")
                
        except Exception as e:
            logger.error(f"Error in /addadmin: {e}")

    @bot.message_handler(commands=['removeadmin'])
    def remove_admin_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_main_admin(user_id):
                bot.send_message(user_id, "❌ Только главный администратор может удалять админов")
                return
                
            args = message.text.split()[1:]
            if len(args) < 1:
                bot.send_message(user_id, "❌ Использование: /removeadmin [id]")
                return
                
            target_id = int(args[0])
            
            if remove_admin(target_id):
                bot.send_message(user_id, f"✅ Администратор {target_id} удален")
                log_admin_action(message.from_user, f"удалил администратора {target_id}")
            else:
                bot.send_message(user_id, "❌ Ошибка при удалении администратора")
                
        except Exception as e:
            logger.error(f"Error in /removeadmin: {e}")

    @bot.message_handler(commands=['users'])
    def users_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            users = get_all_users()
            if not users:
                bot.send_message(user_id, "📊 Пользователей нет")
                return
                
            users_text = f"📊 Всего пользователей: {len(users)}\n\n"
            for i, user in enumerate(users[:50], 1):  # Показываем первые 50
                user_id, username, first_name, last_name = user
                name = f"{first_name} {last_name}" if last_name else first_name
                users_text += f"{i}. {name} (@{username}) - {user_id}\n"
                
            if len(users) > 50:
                users_text += f"\n... и еще {len(users) - 50} пользователей"
                
            bot.send_message(user_id, users_text)
            log_admin_action(message.from_user, "просмотрел список пользователей")
            
        except Exception as e:
            logger.error(f"Error in /users: {e}")

    @bot.message_handler(commands=['admins'])
    def admins_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            admins = get_all_admins()
            admins_text = "👑 Список администраторов:\n\n"
            
            for admin in admins:
                admin_id, username, first_name, is_main = admin
                role = "Главный" if is_main else "Обычный"
                admins_text += f"{first_name} (@{username}) - {admin_id} - {role}\n"
                
            bot.send_message(user_id, admins_text)
            log_admin_action(message.from_user, "просмотрел список администраторов")
            
        except Exception as e:
            logger.error(f"Error in /admins: {e}")

    @bot.message_handler(commands=['stats'])
    def stats_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            user_count = get_user_count()
            admins = get_all_admins()
            stats = get_promocode_stats()
            
            stats_text = (
                "📊 Статистика бота:\n\n"
                f"👥 Пользователей: {user_count}\n"
                f"👑 Админов: {len(admins)}\n"
                f"🎫 Промокодов всего: {stats['total']}\n"
                f"✅ Использовано: {stats['used']}\n"
                f"🆓 Доступно: {stats['available']}\n"
                f"🕒 Время сервера: {get_current_time()}"
            )
            bot.send_message(user_id, stats_text)
            log_admin_action(message.from_user, "просмотрел статистику")
            
        except Exception as e:
            logger.error(f"Error in /stats: {e}")

    @bot.message_handler(commands=['clearlogs'])
    def clear_logs_command(message):
        try:
            user_id = message.from_user.id
            
            if not is_admin(user_id):
                bot.send_message(user_id, "❌ У вас нет прав для этой команды")
                return
                
            try:
                with open(ADMIN_LOGFILE, 'w', encoding='utf-8') as f:
                    f.write('')
                bot.send_message(user_id, "✅ Логи администраторов очищены")
                log_admin_action(message.from_user, "очистил логи администраторов")
            except Exception as e:
                bot.send_message(user_id, "❌ Ошибка при очистке логов")
                logger.error(f"Error clearing logs: {e}")
                
        except Exception as e:
            logger.error(f"Error in /clearlogs: {e}")

    # Обработка кнопок
    @bot.message_handler(func=lambda message: message.text == "📞 Попросить связаться со мной.")
    def request_contact(message):
        try:
            user_id = message.from_user.id
            
            ban_info = is_banned(user_id)
            if ban_info:
                bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту функцию")
                return
                
            cooldown = check_button_cooldown(user_id)
            if cooldown > 0:
                bot.send_message(user_id, f"⏳ Кнопка будет доступна через {int(cooldown)} секунд")
                return
                
            # Убираем кнопку
            bot.send_message(user_id, "✅ Ваш запрос отправлен! Ожидайте ответа.", reply_markup=ReplyKeyboardRemove())
            
            # Восстанавливаем кнопку через 30 секунд
            restore_button(user_id)
            
            # Уведомляем админов
            admins = get_all_admins()
            user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            
            for admin in admins:
                try:
                    admin_id = admin[0]
                    bot.send_message(admin_id, f"📞 Пользователь {user_info} (ID: {user_id}) просит связаться с ним")
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin[0]} about contact request: {e}")
                    
            log_user_action(message.from_user, "запросил связь")
            
        except Exception as e:
            logger.error(f"Error in contact request: {e}")

    @bot.message_handler(func=lambda message: message.text == "🎰 Запустить бурмалду")
    def start_casino_button(message):
        play_casino(message)

    @bot.message_handler(func=lambda message: message.text == "🎁 Запросить промокод")
    def request_promo_button(message):
        request_promo(message)

    # Обработка callback кнопок
    @bot.callback_query_handler(func=lambda call: True)
    def handle_callback(call):
        try:
            user_id = call.from_user.id
            
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ У вас нет прав для этого действия")
                return
                
            if call.data.startswith('unban_'):
                target_id = int(call.data.split('_')[1])
                
                if unban_user(target_id):
                    bot.answer_callback_query(call.id, "✅ Пользователь разбанен")
                    bot.edit_message_text(
                        f"✅ Пользователь {target_id} разбанен администратором {call.from_user.first_name}",
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id
                    )
                    
                    try:
                        bot.send_message(target_id, "✅ Вы были разбанены администратором")
                    except:
                        pass
                        
                    log_admin_action(call.from_user, f"разбанил пользователя {target_id} через кнопку")
                else:
                    bot.answer_callback_query(call.id, "❌ Ошибка при разбане")
                    
        except Exception as e:
            logger.error(f"Error in callback handler: {e}")

    # Обработка обычных сообщений (пересылка админам)
    @bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'audio', 'voice'])
    def forward_to_admins(message):
        try:
            user_id = message.from_user.id
            
            ban_info = is_banned(user_id)
            if ban_info:
                bot.send_message(user_id, "🚫 Вы забанены и не можете отправлять сообщения")
                return
                
            # Проверяем кулдаун
            cooldown = check_cooldown(user_id)
            if cooldown > 0:
                bot.send_message(user_id, f"⏳ Подождите {int(cooldown)} секунд перед отправкой следующего сообщения")
                return
                
            register_user(user_id,
                         message.from_user.username,
                         message.from_user.first_name,
                         message.from_user.last_name)
            
            # Пересылаем сообщение всем админам
            admins = get_all_admins()
            user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
            
            for admin in admins:
                try:
                    admin_id = admin[0]
                    
                    # Создаем клавиатуру для ответа
                    markup = InlineKeyboardMarkup()
                    markup.add(InlineKeyboardButton("📨 Ответить", callback_data=f"reply_{user_id}"))
                    
                    # Пересылаем сообщение в зависимости от типа
                    if message.content_type == 'text':
                        bot.send_message(admin_id, 
                                       f"📩 Сообщение от {user_info} (ID: {user_id}):\n\n{message.text}",
                                       reply_markup=markup)
                    else:
                        # Для медиа-сообщений сначала отправляем текст, потом медиа
                        caption = f"📩 Сообщение от {user_info} (ID: {user_id})"
                        if message.caption:
                            caption += f"\n\n{message.caption}"
                            
                        if message.content_type == 'photo':
                            bot.send_photo(admin_id, message.photo[-1].file_id, caption=caption, reply_markup=markup)
                        elif message.content_type == 'video':
                            bot.send_video(admin_id, message.video.file_id, caption=caption, reply_markup=markup)
                        elif message.content_type == 'document':
                            bot.send_document(admin_id, message.document.file_id, caption=caption, reply_markup=markup)
                        elif message.content_type == 'audio':
                            bot.send_audio(admin_id, message.audio.file_id, caption=caption, reply_markup=markup)
                        elif message.content_type == 'voice':
                            bot.send_voice(admin_id, message.voice.file_id, caption=caption, reply_markup=markup)
                            
                except Exception as e:
                    logger.error(f"Failed to forward message to admin {admin[0]}: {e}")
                    
            bot.send_message(user_id, "✅ Ваше сообщение отправлено!")
            log_user_action(message.from_user, "отправил сообщение")
            
        except Exception as e:
            logger.error(f"Error in message forwarding: {e}")

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
    
    # Настройка webhook
    bot.remove_webhook()
    time.sleep(2)
    bot.set_webhook(url=f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook")
    
    logger.info("🤖 Webhook configured - bot is ready!")
    
    # ЗАПУСКАЕМ FLASK НЕ В ПОТОКЕ, А В ОСНОВНОМ ПРОЦЕССЕ
    if __name__ == "__main__":
        logger.info("🚀 Starting Flask app directly...")
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
        
else:
    # Локально используем polling (ТОЛЬКО для разработки)
    if __name__ == "__main__":
        ensure_log_files()
        init_db()
        
        try:
            logger.info("🚀 Starting bot in POLLING mode (local development)")
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            logger.exception("Polling error: %s", e)
