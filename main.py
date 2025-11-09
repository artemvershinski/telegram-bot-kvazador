#!/usr/bin/env python3
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
from flask import Flask, request
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton

LOGFILE = os.environ.get("BOT_LOGFILE", "bot.log")
ADMIN_LOGFILE = os.environ.get("ADMIN_LOGFILE", "admin_actions.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGFILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

admin_logger = logging.getLogger('admin_actions')
admin_logger.setLevel(logging.INFO)
admin_handler = logging.FileHandler(ADMIN_LOGFILE, encoding='utf-8')
admin_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
admin_logger.addHandler(admin_handler)
admin_logger.propagate = False

def get_db_connection():
    try:
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
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
    return datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

def ensure_log_files():
    try:
        for log_file in [LOGFILE, ADMIN_LOGFILE]:
            if not os.path.exists(log_file):
                open(log_file, 'w', encoding='utf-8').close()
                logger.info(f"Created log file: {log_file}")
    except Exception as e:
        logger.error(f"Failed to create log files: {e}")

def format_admin_name(user):
    if user.username:
        return f"@{user.username}"
    return user.first_name or "Unknown"

def format_target_info(user_id, username=None, first_name=None):
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

def parse_log_line(line):
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
    grouped = defaultdict(list)
    for log in logs:
        timestamp_str, content = parse_log_line(log)
        if timestamp_str and content:
            date_part = timestamp_str.split()[0]
            time_part = timestamp_str.split()[1] if ' ' in timestamp_str else "00:00:00"
            grouped[date_part].append((time_part, content))
    return grouped

def format_admin_logs_for_display(logs, days=30):
    if not logs:
        return "Логов не найдено"
    grouped_logs = group_logs_by_date(logs)
    if not grouped_logs:
        return "Логов не найдено"
    result = ""
    sorted_dates = sorted(grouped_logs.keys(), reverse=True)
    for date in sorted_dates:
        result += f"════════ {date} ════════\n"
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

def get_main_user_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Игры", callback_data="user_games"),
        InlineKeyboardButton("Промокоды", callback_data="user_promocodes"),
        InlineKeyboardButton("Поддержка", callback_data="user_support"),
        InlineKeyboardButton("Топ игроков", callback_data="user_top"),
        InlineKeyboardButton("Помощь", callback_data="user_help"),
        InlineKeyboardButton("Баланс", callback_data="user_balance")
    )
    return keyboard

def get_games_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Слоты", callback_data="game_slots"),
        InlineKeyboardButton("Blackjack", callback_data="game_blackjack"),
        InlineKeyboardButton("Назад", callback_data="user_back_main")
    )
    return keyboard

def get_promocodes_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Запросить промокод", callback_data="promo_request"),
        InlineKeyboardButton("Активировать промокод", callback_data="promo_activate"),
        InlineKeyboardButton("Назад", callback_data="user_back_main")
    )
    return keyboard

def get_bet_keyboard_inline():
    keyboard = InlineKeyboardMarkup(row_width=3)
    keyboard.add(
        InlineKeyboardButton("100", callback_data="bet_100"),
        InlineKeyboardButton("500", callback_data="bet_500"),
        InlineKeyboardButton("1000", callback_data="bet_1000"),
        InlineKeyboardButton("Все", callback_data="bet_all"),
        InlineKeyboardButton("Своя ставка", callback_data="bet_custom"),
        InlineKeyboardButton("Назад", callback_data="user_back_main")
    )
    return keyboard

def get_back_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("Назад", callback_data="user_back_main"))
    return keyboard

def get_main_admin_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
        InlineKeyboardButton("🚫 Баны", callback_data="admin_bans"),
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("🔧 Инструменты", callback_data="admin_tools"),
        InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton("❓ Помощь", callback_data="admin_help")
    )
    return keyboard

def get_admin_users_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Список", callback_data="admin_users_list"),
        InlineKeyboardButton("Найти", callback_data="admin_users_find"),
        InlineKeyboardButton("Ответить", callback_data="admin_users_reply"),
        InlineKeyboardButton("Назад", callback_data="admin_back_main")
    )
    return keyboard

def get_admin_bans_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Забанить", callback_data="admin_ban"),
        InlineKeyboardButton("Разбанить", callback_data="admin_razban"),
        InlineKeyboardButton("Список банов", callback_data="admin_bans_list"),
        InlineKeyboardButton("Назад", callback_data="admin_back_main")
    )
    return keyboard

def get_admin_stats_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Пользователи", callback_data="admin_stats_users"),
        InlineKeyboardButton("Промокоды", callback_data="admin_stats_promo"),
        InlineKeyboardButton("Размер БД", callback_data="admin_stats_db"),
        InlineKeyboardButton("Логи", callback_data="admin_stats_logs"),
        InlineKeyboardButton("Назад", callback_data="admin_back_main")
    )
    return keyboard

def get_admin_tools_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Создать промокод", callback_data="admin_tools_promo"),
        InlineKeyboardButton("Очистить логи", callback_data="admin_tools_clearlogs"),
        InlineKeyboardButton("Перезапуск", callback_data="admin_tools_restart"),
        InlineKeyboardButton("Назад", callback_data="admin_back_main")
    )
    return keyboard

user_last_message_time = {}
MESSAGE_COOLDOWN = 5

def check_cooldown(user_id):
    current_time = time.time()
    last_time = user_last_message_time.get(user_id, 0)
    time_passed = current_time - last_time
    if time_passed < MESSAGE_COOLDOWN:
        return MESSAGE_COOLDOWN - time_passed
    user_last_message_time[user_id] = current_time
    return 0

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Bot is alive and running! TG: @werb"

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

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    logger.error("BOT_TOKEN not found in environment. Please set BOT_TOKEN.")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8401905691"))

bot = telebot.TeleBot(BOT_TOKEN) if BOT_TOKEN else None

def init_db():
    try:
        logger.info(f"Initializing database with ADMIN_ID: {ADMIN_ID}")
        def _init():
            conn = get_db_connection()
            c = conn.cursor()
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
            c.execute("""
                INSERT INTO admins (user_id, username, first_name, is_main_admin) 
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO NOTHING
            """, (ADMIN_ID, "werb", "werb", True))
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
    def _check():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        conn.close()
        return result is not None
    return safe_db_execute(_check)

def is_main_admin(user_id):
    def _check():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id FROM admins WHERE user_id = %s AND is_main_admin = TRUE", (user_id,))
        result = c.fetchone()
        conn.close()
        return result is not None
    return safe_db_execute(_check)

def add_admin(user_id, username, first_name):
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
    def _get_users():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id, username, first_name, last_name FROM users")
        users = c.fetchall()
        conn.close()
        return users
    return safe_db_execute(_get_users)

def get_user_count():
    def _get_count():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        count = c.fetchone()[0]
        conn.close()
        return count
    return safe_db_execute(_get_count)

def get_all_admins():
    def _get_admins():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id, username, first_name, is_main_admin FROM admins")
        admins = c.fetchall()
        conn.close()
        return admins
    return safe_db_execute(_get_admins)

def get_top_users(limit=10):
    def _get_top():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT u.user_id, u.username, u.first_name, u.last_name, ub.balance 
            FROM users u 
            JOIN user_balance ub ON u.user_id = ub.user_id 
            ORDER BY ub.balance DESC 
            LIMIT %s
        """, (limit,))
        users = c.fetchall()
        conn.close()
        return users
    return safe_db_execute(_get_top)

def get_db_size():
    def _get_size():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
            SELECT 
                table_name,
                pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) as size
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY pg_total_relation_size(quote_ident(table_name)) DESC
        ''')
        tables = c.fetchall()
        c.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        total_size = c.fetchone()[0]
        conn.close()
        return {
            'tables': tables,
            'total_size': total_size
        }
    return safe_db_execute(_get_size)

def ban_user(user_id, ban_type, duration_seconds=None, reason="", banned_by=None):
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
    def _update():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE bans SET unban_request_date = CURRENT_TIMESTAMP WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
        return True
    return safe_db_execute(_update)

def get_user_balance(user_id):
    def _get_balance():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT balance FROM user_balance WHERE user_id = %s", (user_id,))
        result = c.fetchone()
        conn.close()
        return result[0] if result else 0
    return safe_db_execute(_get_balance)

def update_user_balance(user_id, new_balance):
    def _update():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE user_balance SET balance = %s WHERE user_id = %s", (new_balance, user_id))
        conn.commit()
        conn.close()
        return True
    return safe_db_execute(_update)

def add_promocode(promocode, value):
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
    def _use():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT value, used FROM promocodes WHERE promocode = %s", (promocode,))
        result = c.fetchone()
        if not result:
            return None, "Промокод не найден"
        value, used = result
        if used:
            return None, "Промокод уже использован"
        c.execute("UPDATE promocodes SET used = TRUE, used_by = %s WHERE promocode = %s", (user_id, promocode))
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

def calculate_win(lines, bet):
    total_win = 0
    winning_lines = []
    multipliers = {
        "🍒": {"3": 1.5},
        "🍋": {"3": 2},
        "🍊": {"3": 3},
        "🍇": {"3": 4},
        "💎": {"3": 6},
        "7️⃣": {"3": 12}
    }
    for i, line in enumerate(lines, 1):
        symbols = line
        if symbols[0] == symbols[1] == symbols[2]:
            symbol = symbols[0]
            if symbol in multipliers:
                win_amount = bet * multipliers[symbol]["3"]
                total_win += win_amount
                winning_lines.append(f"Линия {i}: {symbol*3} x{multipliers[symbol]['3']} = {win_amount}")
    return total_win, winning_lines

def check_all_lines(result):
    lines = []
    lines.append([result[0][0], result[0][1], result[0][2]])
    lines.append([result[1][0], result[1][1], result[1][2]])
    lines.append([result[2][0], result[2][1], result[2][2]])
    lines.append([result[0][0], result[1][0], result[2][0]])
    lines.append([result[0][1], result[1][1], result[2][1]])
    lines.append([result[0][2], result[1][2], result[2][2]])
    lines.append([result[0][0], result[1][1], result[2][2]])
    lines.append([result[0][2], result[1][1], result[2][0]])
    return lines

def spin_slots_animation(bot, chat_id, message_id, bet_amount, user_id):
    symbols = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
    final_result = [
        [random.choice(symbols) for _ in range(3)],
        [random.choice(symbols) for _ in range(3)],
        [random.choice(symbols) for _ in range(3)]
    ]
    empty_grid = "⬜️⬜️⬜️\n⬜️⬜️⬜️\n⬜️⬜️⬜️"
    bot.edit_message_text(
        f"🎰 НАЧИНАЕМ... 🎰\nСтавка: {bet_amount}\n{empty_grid}",
        chat_id=chat_id,
        message_id=message_id
    )
    time.sleep(0.5)
    for frame in range(6):
        display = [
            [random.choice(symbols) for _ in range(3)],
            [random.choice(symbols) for _ in range(3)],
            [random.choice(symbols) for _ in range(3)]
        ]
        grid_text = f"{''.join(display[0])}\n{''.join(display[1])}\n{''.join(display[2])}"
        try:
            bot.edit_message_text(
                f"🎰 КРУТИМ... 🎰\nСтавка: {bet_amount}\n{grid_text}",
                chat_id=chat_id,
                message_id=message_id
            )
        except:
            pass
        time.sleep(0.25)
    for i in range(3):
        final_result[i][0] = random.choice(symbols)
    grid_text = f"{''.join(final_result[0])}\n{''.join(final_result[1])}\n{''.join(final_result[2])}"
    try:
        bot.edit_message_text(
            f"🎰 ОСТАНАВЛИВАЕМ... 🎰\nСтавка: {bet_amount}\n{grid_text}",
            chat_id=chat_id,
            message_id=message_id
        )
    except:
        pass
    time.sleep(0.5)
    for i in range(3):
        final_result[i][1] = random.choice(symbols)
    grid_text = f"{''.join(final_result[0])}\n{''.join(final_result[1])}\n{''.join(final_result[2])}"
    try:
        bot.edit_message_text(
            f"🎰 ОСТАНАВЛИВАЕМ... 🎰\nСтавка: {bet_amount}\n{grid_text}",
            chat_id=chat_id,
            message_id=message_id
        )
    except:
        pass
    time.sleep(0.5)
    for i in range(3):
        final_result[i][2] = random.choice(symbols)
    grid_text = f"{''.join(final_result[0])}\n{''.join(final_result[1])}\n{''.join(final_result[2])}"
    try:
        bot.edit_message_text(
            f"🎰 РЕЗУЛЬТАТ 🎰\nСтавка: {bet_amount}\n{grid_text}",
            chat_id=chat_id,
            message_id=message_id
        )
    except:
        pass
    time.sleep(0.5)
    return final_result

def create_deck():
    suits = ['♠️', '♥️', '♦️', '♣️']
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    deck = [f'{rank}{suit}' for suit in suits for rank in ranks]
    random.shuffle(deck)
    return deck

def calculate_hand_value(hand):
    value = 0
    aces = 0
    for card in hand:
        rank = card[:-2]
        if rank in ['J', 'Q', 'K']:
            value += 10
        elif rank == 'A':
            value += 11
            aces += 1
        else:
            value += int(rank)
    while value > 21 and aces > 0:
        value -= 10
        aces -= 1
    return value

def format_hand(hand, hide_dealer=False):
    if hide_dealer and len(hand) > 1:
        return f"[{hand[0]}, ❓]"
    return "[" + ", ".join(hand) + "]"

def get_blackjack_keyboard(game_state="playing"):
    keyboard = InlineKeyboardMarkup(row_width=2)
    if game_state == "playing":
        keyboard.add(
            InlineKeyboardButton("⬆️ Еще карту", callback_data="bj_hit"),
            InlineKeyboardButton("✋ Хватит", callback_data="bj_stand"),
            InlineKeyboardButton("💰 Удвоить", callback_data="bj_double"),
            InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
        )
    else:
        keyboard.add(
            InlineKeyboardButton("🔄 Сыграть еще", callback_data="game_blackjack"),
            InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
        )
    return keyboard

# Глобальные переменные для хранения состояний
user_reply_mode = {}
user_unban_mode = {}
user_bet_mode = {}
user_custom_bet_mode = {}
user_broadcast_mode = {}
user_support_mode = {}

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
        
        # Удаляем предыдущее сообщение если есть
        try:
            if message.message_id:
                bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        register_user(user_id,
                      message.from_user.username,
                      message.from_user.first_name,
                      message.from_user.last_name)
        balance = get_user_balance(user_id)
        welcome_text = f"Добро пожаловать в WERB HUB\n\nБаланс: {balance} монет"
        sent_msg = bot.send_message(
            user_id, 
            welcome_text, 
            reply_markup=get_main_user_keyboard()
        )
        log_user_action(message.from_user, "start")
    except Exception as e:
        logger.exception("Error in /start handler for message: %s", message)

@bot.callback_query_handler(func=lambda call: call.data.startswith('user_'))
def handle_user_callbacks(call):
    user_id = call.from_user.id
    balance = get_user_balance(user_id)
    try:
        if call.data == 'user_games':
            bot.edit_message_text(
                "Выберите игру:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_games_keyboard()
            )
        elif call.data == 'user_promocodes':
            bot.edit_message_text(
                "Промокоды:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_promocodes_keyboard()
            )
        elif call.data == 'user_support':
            # Включаем режим поддержки для пользователя
            user_support_mode[user_id] = True
            bot.edit_message_text(
                "💬 Режим поддержки включен\n\nНапишите ваше сообщение и отправьте его\nАдминистратор ответит здесь или в ЛС\n\nДля выхода из режима нажмите 'Назад'",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_back_keyboard()
            )
        elif call.data == 'user_top':
            try:
                top_users = get_top_users(10)
                top_text = "ТОП-10 ИГРОКОВ\n\n"
                for i, user in enumerate(top_users, 1):
                    top_user_id, username, first_name, last_name, balance = user
                    name = f"@{username}" if username else first_name
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                    top_text += f"{medal} {name} - {balance:,} монет\n"
                keyboard = InlineKeyboardMarkup()
                keyboard.add(InlineKeyboardButton("Назад", callback_data="user_back_main"))
                bot.edit_message_text(
                    top_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Error getting top users: {e}")
                bot.edit_message_text(
                    "Ошибка при получении топа игроков",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_back_keyboard()
                )
        elif call.data == 'user_help':
            help_text = (
                "Доступные команды:\n\n"
                "/start - Главное меню\n"
                "/casino - Слоты\n"
                "/blackjack - Blackjack\n"
                "/balance - Баланс\n"
                "/top - Топ игроков\n"
                "/promo - Активировать промокод\n"
                "/get_promo - Запросить промокод\n"
                "/unban - Запрос разбана"
            )
            bot.edit_message_text(
                help_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_back_keyboard()
            )
        elif call.data == 'user_balance':
            bot.edit_message_text(
                f"Баланс: {balance} монет",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_back_keyboard()
            )
        elif call.data == 'user_back_main':
            # Выключаем все режимы пользователя
            user_support_mode.pop(user_id, None)
            user_bet_mode.pop(user_id, None)
            user_custom_bet_mode.pop(user_id, None)
            
            welcome_text = f"Добро пожаловать в WERB HUB\n\nБаланс: {balance} монет"
            bot.edit_message_text(
                welcome_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_user_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in user callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")

@bot.callback_query_handler(func=lambda call: call.data.startswith('game_'))
def handle_game_callbacks(call):
    user_id = call.from_user.id
    balance = get_user_balance(user_id)
    try:
        if call.data == 'game_slots':
            if balance < 100:
                bot.answer_callback_query(call.id, "❌ Минимум 100 монет для игры")
                return
            bot.edit_message_text(
                f"Слоты\nБаланс: {balance} монет\n\nВыберите ставку:\nМин: 100 монет\nМакс: {balance}",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_bet_keyboard_inline()
            )
        elif call.data == 'game_blackjack':
            if balance < 100:
                bot.answer_callback_query(call.id, "❌ Минимум 100 монет для игры")
                return
            bot.edit_message_text(
                f"Blackjack\nБаланс: {balance} монет\n\nВыберите ставку:\nМин: 100 монет\nМакс: {balance}",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_bet_keyboard_inline()
            )
    except Exception as e:
        logger.error(f"Error in game callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")

@bot.callback_query_handler(func=lambda call: call.data.startswith('promo_'))
def handle_promo_callbacks(call):
    user_id = call.from_user.id
    try:
        if call.data == 'promo_request':
            admins = get_all_admins()
            user_info = f"@{call.from_user.username}" if call.from_user.username else call.from_user.first_name
            notified = False
            for admin in admins:
                try:
                    admin_id = admin[0]
                    bot.send_message(admin_id, f"🎫 Пользователь {user_info} (ID: {user_id}) запросил промокод")
                    notified = True
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin[0]} about promo request: {e}")
            if notified:
                bot.edit_message_text(
                    "Запрос отправлен администраторам\nОжидайте создания промокода",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_back_keyboard()
                )
                log_user_action(call.from_user, "request_promo")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка при отправке запроса")
        elif call.data == 'promo_activate':
            bot.edit_message_text(
                "Введите промокод:\n/promo КОД",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_back_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in promo callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")

@bot.callback_query_handler(func=lambda call: call.data.startswith('bet_'))
def handle_bet_callbacks(call):
    user_id = call.from_user.id
    balance = get_user_balance(user_id)
    try:
        if call.data in ['bet_100', 'bet_500', 'bet_1000']:
            bet_amount = int(call.data.split('_')[1])
            if balance < bet_amount:
                bot.answer_callback_query(call.id, "❌ Недостаточно средств")
                return
            if call.message.text.startswith("Слоты"):
                final_result = spin_slots_animation(bot, call.message.chat.id, call.message.message_id, bet_amount, user_id)
                all_lines = check_all_lines(final_result)
                total_win, winning_lines = calculate_win(all_lines, bet_amount)
                if total_win > 0:
                    new_balance = balance - bet_amount + total_win
                    update_user_balance(user_id, new_balance)
                    result_text = f"🎉 ВЫИГРЫШ\n\n💵 Ставка: {bet_amount}\n💰 Выигрыш: {total_win}\n💎 Баланс: {new_balance}\n\n"
                    if winning_lines:
                        result_text += "🏆 Линии:\n" + "\n".join(winning_lines[:3])
                else:
                    new_balance = balance - bet_amount
                    update_user_balance(user_id, new_balance)
                    result_text = f"😞 ПРОИГРЫШ\n\n💵 Ставка: {bet_amount}\n💎 Баланс: {new_balance}"
                keyboard = InlineKeyboardMarkup()
                keyboard.add(
                    InlineKeyboardButton("🔄 Сыграть еще", callback_data="game_slots"),
                    InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
                )
                bot.edit_message_text(
                    result_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=keyboard
                )
                log_user_action(call.from_user, f"сыграл в слоты: ставка {bet_amount}, выигрыш {total_win}")
            elif call.message.text.startswith("Blackjack"):
                user_blackjack_games[user_id] = {
                    'deck': create_deck(),
                    'player_hand': [],
                    'dealer_hand': [],
                    'bet': bet_amount,
                    'message_id': call.message.message_id
                }
                game = user_blackjack_games[user_id]
                game['player_hand'] = [game['deck'].pop(), game['deck'].pop()]
                game['dealer_hand'] = [game['deck'].pop(), game['deck'].pop()]
                player_value = calculate_hand_value(game['player_hand'])
                dealer_value = calculate_hand_value([game['dealer_hand'][0]])
                game_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'], hide_dealer=True)}\n\nСтавка: {bet_amount}"
                bot.edit_message_text(
                    game_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_blackjack_keyboard()
                )
        elif call.data == 'bet_all':
            if balance < 100:
                bot.answer_callback_query(call.id, "❌ Недостаточно средств")
                return
            bet_amount = balance
            if call.message.text.startswith("Слоты"):
                final_result = spin_slots_animation(bot, call.message.chat.id, call.message.message_id, bet_amount, user_id)
                all_lines = check_all_lines(final_result)
                total_win, winning_lines = calculate_win(all_lines, bet_amount)
                if total_win > 0:
                    new_balance = balance - bet_amount + total_win
                    update_user_balance(user_id, new_balance)
                    result_text = f"🎉 ВЫИГРЫШ\n\n💵 Ставка: {bet_amount}\n💰 Выигрыш: {total_win}\n💎 Баланс: {new_balance}\n\n"
                    if winning_lines:
                        result_text += "🏆 Линии:\n" + "\n".join(winning_lines[:3])
                else:
                    new_balance = 0
                    update_user_balance(user_id, new_balance)
                    result_text = f"😞 ПРОИГРЫШ\n\n💵 Ставка: {bet_amount}\n💎 Баланс: {new_balance}"
                keyboard = InlineKeyboardMarkup()
                keyboard.add(
                    InlineKeyboardButton("🔄 Сыграть еще", callback_data="game_slots"),
                    InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
                )
                bot.edit_message_text(
                    result_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=keyboard
                )
                log_user_action(call.from_user, f"сыграл в слоты: ставка {bet_amount}, выигрыш {total_win}")
            elif call.message.text.startswith("Blackjack"):
                user_blackjack_games[user_id] = {
                    'deck': create_deck(),
                    'player_hand': [],
                    'dealer_hand': [],
                    'bet': bet_amount,
                    'message_id': call.message.message_id
                }
                game = user_blackjack_games[user_id]
                game['player_hand'] = [game['deck'].pop(), game['deck'].pop()]
                game['dealer_hand'] = [game['deck'].pop(), game['deck'].pop()]
                player_value = calculate_hand_value(game['player_hand'])
                dealer_value = calculate_hand_value([game['dealer_hand'][0]])
                game_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'], hide_dealer=True)}\n\nСтавка: {bet_amount}"
                bot.edit_message_text(
                    game_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_blackjack_keyboard()
                )
        elif call.data == 'bet_custom':
            user_custom_bet_mode[user_id] = True
            bot.edit_message_text(
                f"Введите свою ставку (число):\n\nМин: 100 монет\nМакс: {balance}",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_back_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in bet callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка при запуске игры")

@bot.callback_query_handler(func=lambda call: call.data.startswith('bj_'))
def handle_blackjack_callbacks(call):
    user_id = call.from_user.id
    if user_id not in user_blackjack_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена")
        return
    game = user_blackjack_games[user_id]
    balance = get_user_balance(user_id)
    try:
        if call.data == 'bj_hit':
            game['player_hand'].append(game['deck'].pop())
            player_value = calculate_hand_value(game['player_hand'])
            if player_value > 21:
                new_balance = balance - game['bet']
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value}) - ПЕРЕБОР!\nРука дилера: {format_hand(game['dealer_hand'])} ({calculate_hand_value(game['dealer_hand'])})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n😞 Вы проиграли!"
                bot.edit_message_text(
                    result_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_blackjack_keyboard("finished")
                )
                del user_blackjack_games[user_id]
                log_user_action(call.from_user, f"сыграл в blackjack: ставка {game['bet']}, проигрыш")
            else:
                game_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'], hide_dealer=True)}\n\nСтавка: {game['bet']}"
                bot.edit_message_text(
                    game_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_blackjack_keyboard()
                )
        elif call.data == 'bj_stand':
            player_value = calculate_hand_value(game['player_hand'])
            while calculate_hand_value(game['dealer_hand']) < 17:
                game['dealer_hand'].append(game['deck'].pop())
            dealer_value = calculate_hand_value(game['dealer_hand'])
            if dealer_value > 21 or player_value > dealer_value:
                win_amount = game['bet'] * 2
                new_balance = balance - game['bet'] + win_amount
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💰 Выигрыш: {win_amount}\n💎 Баланс: {new_balance}\n\n🎉 Вы выиграли!"
            elif player_value == dealer_value:
                new_balance = balance
                result_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n🤝 Ничья!"
            else:
                new_balance = balance - game['bet']
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n😞 Вы проиграли!"
            bot.edit_message_text(
                result_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_blackjack_keyboard("finished")
            )
            del user_blackjack_games[user_id]
            log_user_action(call.from_user, f"сыграл в blackjack: ставка {game['bet']}, результат")
        elif call.data == 'bj_double':
            if balance < game['bet'] * 2:
                bot.answer_callback_query(call.id, "❌ Недостаточно средств для удвоения")
                return
            game['bet'] *= 2
            game['player_hand'].append(game['deck'].pop())
            player_value = calculate_hand_value(game['player_hand'])
            while calculate_hand_value(game['dealer_hand']) < 17:
                game['dealer_hand'].append(game['deck'].pop())
            dealer_value = calculate_hand_value(game['dealer_hand'])
            if player_value > 21:
                new_balance = balance - game['bet']
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value}) - ПЕРЕБОР!\nРука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n😞 Вы проиграли!"
            elif dealer_value > 21 or player_value > dealer_value:
                win_amount = game['bet'] * 2
                new_balance = balance - game['bet'] + win_amount
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💰 Выигрыш: {win_amount}\n💎 Баланс: {new_balance}\n\n🎉 Вы выиграли!"
            elif player_value == dealer_value:
                new_balance = balance
                result_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n🤝 Ничья!"
            else:
                new_balance = balance - game['bet']
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\nВаша рука: {format_hand(game['player_hand'])} ({player_value})\nРука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n😞 Вы проиграли!"
            bot.edit_message_text(
                result_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_blackjack_keyboard("finished")
            )
            del user_blackjack_games[user_id]
            log_user_action(call.from_user, f"сыграл в blackjack: удвоение, ставка {game['bet']}, результат")
    except Exception as e:
        logger.error(f"Error in blackjack callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка в игре")

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            bot.send_message(user_id, "❌ Нет прав")
            return
            
        # Удаляем команду админа
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        bot.send_message(
            user_id,
            "АДМИН ПАНЕЛЬ",
            reply_markup=get_main_admin_keyboard()
        )
        log_admin_action(message.from_user, "открыл админ панель")
    except Exception as e:
        logger.error(f"Error in /admin: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def handle_admin_callbacks(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "❌ Нет прав")
        return
    try:
        if call.data == 'admin_back_main':
            # Выключаем все режимы админа
            user_reply_mode.pop(user_id, None)
            user_broadcast_mode.pop(user_id, None)
            
            bot.edit_message_text(
                "АДМИН ПАНЕЛЬ",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_admin_keyboard()
            )
        elif call.data == 'admin_users':
            bot.edit_message_text(
                "УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_users_keyboard()
            )
        elif call.data == 'admin_bans':
            bot.edit_message_text(
                "УПРАВЛЕНИЕ БАНАМИ",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_bans_keyboard()
            )
        elif call.data == 'admin_stats':
            bot.edit_message_text(
                "СТАТИСТИКА",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_stats_keyboard()
            )
        elif call.data == 'admin_tools':
            bot.edit_message_text(
                "ИНСТРУМЕНТЫ",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_tools_keyboard()
            )
        elif call.data == 'admin_broadcast':
            user_broadcast_mode[user_id] = True
            bot.edit_message_text(
                "📨 РАССЫЛКА\n\nВведите текст или отправьте медиа для рассылки всем пользователям:\n\nДля отмены нажмите 'Назад'",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="admin_back_main"))
            )
        elif call.data == 'admin_help':
            help_text = (
                "АДМИН КОМАНДЫ\n\n"
                "/ban ID время причина\n"
                "/razban ID\n"
                "/reply ID\n"
                "/add_promo код сумма\n"
                "/adminlogs дни\n\n"
                "Используйте админ-панель для удобного управления!"
            )
            bot.edit_message_text(
                help_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="admin_back_main"))
            )
        elif call.data == 'admin_users_list':
            users = get_all_users()
            users_text = f"👥 Всего пользователей: {len(users)}\n\n"
            for i, user in enumerate(users[:20], 1):
                user_id, username, first_name, last_name = user
                name = f"{first_name} {last_name}" if last_name else first_name
                users_text += f"{i}. {name} (@{username}) - {user_id}\n"
            if len(users) > 20:
                users_text += f"\n... и еще {len(users) - 20} пользователей"
            bot.edit_message_text(
                users_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_users_keyboard()
            )
        elif call.data == 'admin_ban':
            bot.edit_message_text(
                "Забанить пользователя:\n/ban ID время причина\n\nПример: /ban 1234567 3600 Спам",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_bans_keyboard()
            )
        elif call.data == 'admin_razban':
            bot.edit_message_text(
                "Разбанить пользователя:\n/razban ID\n\nПример: /razban 1234567",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_bans_keyboard()
            )
        elif call.data == 'admin_tools_promo':
            bot.edit_message_text(
                "Создать промокод:\n/add_promo код сумма\n\nПример: /add_promo SUMMER2024 1000",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_tools_keyboard()
            )
        elif call.data == 'admin_stats_users':
            user_count = get_user_count()
            stats_text = f"📊 Статистика пользователей\n\n👥 Всего пользователей: {user_count}"
            bot.edit_message_text(
                stats_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_stats_keyboard()
            )
        elif call.data == 'admin_stats_promo':
            stats = get_promocode_stats()
            stats_text = (
                f"📊 Статистика промокодов\n\n"
                f"🎫 Всего: {stats['total']}\n"
                f"✅ Использовано: {stats['used']}\n"
                f"🆓 Доступно: {stats['available']}"
            )
            bot.edit_message_text(
                stats_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_admin_stats_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in admin callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")

# Обработчик для режима рассылки
@bot.message_handler(func=lambda message: message.from_user.id in user_broadcast_mode and not message.text.startswith('/'))
def handle_broadcast_message(message):
    try:
        admin_id = message.from_user.id
        if admin_id not in user_broadcast_mode:
            return
            
        # Удаляем сообщение админа
        try:
            bot.delete_message(admin_id, message.message_id)
        except:
            pass
            
        users = get_all_users()
        success_count = 0
        fail_count = 0
        
        # Отправляем сообщение о начале рассылки
        progress_msg = bot.send_message(admin_id, f"📨 Начинаем рассылку для {len(users)} пользователей...")
        
        for user in users:
            try:
                user_id = user[0]
                if message.content_type == 'text':
                    bot.send_message(user_id, f"📢 Рассылка от администратора:\n\n{message.text}")
                elif message.content_type == 'photo':
                    bot.send_photo(user_id, message.photo[-1].file_id, 
                                 caption=f"📢 Рассылка от администратора:\n\n{message.caption}" if message.caption else "📢 Рассылка от администратора")
                elif message.content_type == 'video':
                    bot.send_video(user_id, message.video.file_id,
                                 caption=f"📢 Рассылка от администратора:\n\n{message.caption}" if message.caption else "📢 Рассылка от администратора")
                elif message.content_type == 'document':
                    bot.send_document(user_id, message.document.file_id,
                                    caption=f"📢 Рассылка от администратора:\n\n{message.caption}" if message.caption else "📢 Рассылка от администратора")
                elif message.content_type == 'audio':
                    bot.send_audio(user_id, message.audio.file_id,
                                 caption=f"📢 Рассылка от администратора:\n\n{message.caption}" if message.caption else "📢 Рассылка от администратора")
                elif message.content_type == 'voice':
                    bot.send_voice(user_id, message.voice.file_id,
                                 caption="📢 Рассылка от администратора")
                success_count += 1
            except Exception as e:
                fail_count += 1
                logger.error(f"Failed to send broadcast to {user_id}: {e}")
            
            # Обновляем прогресс каждые 10 отправок
            if (success_count + fail_count) % 10 == 0:
                try:
                    bot.edit_message_text(
                        f"📨 Рассылка...\n✅ Успешно: {success_count}\n❌ Ошибок: {fail_count}",
                        chat_id=admin_id,
                        message_id=progress_msg.message_id
                    )
                except:
                    pass
        
        # Завершаем рассылку
        user_broadcast_mode.pop(admin_id, None)
        bot.edit_message_text(
            f"✅ Рассылка завершена!\n\n📊 Результаты:\n✅ Успешно: {success_count}\n❌ Ошибок: {fail_count}",
            chat_id=admin_id,
            message_id=progress_msg.message_id,
            reply_markup=get_main_admin_keyboard()
        )
        log_admin_action(message.from_user, f"сделал рассылку: успешно {success_count}, ошибок {fail_count}")
        
    except Exception as e:
        logger.error(f"Error in broadcast handler: {e}")
        try:
            bot.send_message(admin_id, "❌ Ошибка при рассылке", reply_markup=get_main_admin_keyboard())
        except:
            pass

# Обработчик для сообщений пользователей в режиме поддержки
@bot.message_handler(func=lambda message: message.from_user.id in user_support_mode and not message.text.startswith('/'))
def handle_support_message(message):
    try:
        user_id = message.from_user.id
        if user_id not in user_support_mode:
            return
            
        # Удаляем сообщение пользователя
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        # Пересылаем сообщение админам
        admins = get_all_admins()
        user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        
        for admin in admins:
            try:
                admin_id = admin[0]
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("📨 Ответить", callback_data=f"reply_{user_id}"))
                
                if message.content_type == 'text':
                    bot.send_message(admin_id, 
                                   f"💬 Сообщение в поддержку от {user_info} (ID: {user_id}):\n\n{message.text}",
                                   reply_markup=markup)
                else:
                    caption = f"💬 Сообщение в поддержку от {user_info} (ID: {user_id})"
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
                logger.error(f"Failed to forward support message to admin {admin[0]}: {e}")
        
        # Подтверждаем отправку пользователю
        bot.send_message(user_id, "✅ Ваше сообщение отправлено в поддержку! Ожидайте ответа.")
        log_user_action(message.from_user, "отправил сообщение в поддержку")
        
    except Exception as e:
        logger.error(f"Error in support message handler: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('reply_'))
def handle_reply_callback(call):
    try:
        admin_id = call.from_user.id
        if not is_admin(admin_id):
            bot.answer_callback_query(call.id, "❌ Нет прав")
            return
            
        target_id = int(call.data.split('_')[1])
        user_reply_mode[admin_id] = target_id
        
        # Получаем информацию о пользователе
        user_info = format_target_info(target_id)
        
        bot.answer_callback_query(call.id, "💬 Режим ответа включен")
        bot.send_message(
            admin_id,
            f"💬 Режим ответа включен для пользователя {user_info}\n"
            f"Отправьте сообщение (текст, фото, видео, голосовое и т.д.), которое будет переслано пользователю.\n\n"
            f"Для выхода из режима используйте /stop или нажмите 'Назад' в админ-панели"
        )
        log_admin_action(call.from_user, f"включил режим ответа для {user_info}")
    except Exception as e:
        logger.error(f"Error in reply callback: {e}")

@bot.message_handler(func=lambda message: message.from_user.id in user_reply_mode and not message.text.startswith('/'))
def handle_reply_message(message):
    try:
        admin_id = message.from_user.id
        target_id = user_reply_mode[admin_id]
        
        # Удаляем сообщение админа
        try:
            bot.delete_message(admin_id, message.message_id)
        except:
            pass
            
        try:
            if message.content_type == 'text':
                bot.send_message(target_id, f"📨 Ответ от администратора:\n\n{message.text}")
                bot.send_message(admin_id, f"✅ Ответ отправлен пользователю {format_target_info(target_id)}")
                log_admin_action(message.from_user, f"отправил ответ {target_id}", additional_info=f"текст: {message.text}")
            else:
                caption = "📨 Ответ от администратора"
                if message.caption:
                    caption += f"\n\n{message.caption}"
                    
                if message.content_type == 'photo':
                    bot.send_photo(target_id, message.photo[-1].file_id, caption=caption)
                    media_type = "фото"
                elif message.content_type == 'video':
                    bot.send_video(target_id, message.video.file_id, caption=caption)
                    media_type = "видео"
                elif message.content_type == 'document':
                    bot.send_document(target_id, message.document.file_id, caption=caption)
                    media_type = "документ"
                elif message.content_type == 'audio':
                    bot.send_audio(target_id, message.audio.file_id, caption=caption)
                    media_type = "аудио"
                elif message.content_type == 'voice':
                    bot.send_voice(target_id, message.voice.file_id, caption=caption)
                    media_type = "голосовое сообщение"
                else:
                    media_type = "медиа"
                    
                bot.send_message(admin_id, f"✅ {media_type.capitalize()}-ответ отправлен пользователю {format_target_info(target_id)}")
                log_admin_action(message.from_user, f"отправил ответ {target_id}", additional_info=f"[{media_type}] {caption}")
                
        except Exception as e:
            bot.send_message(admin_id, f"❌ Не удалось отправить сообщение пользователю {target_id}")
            logger.error(f"Failed to send reply to {target_id}: {e}")
    except Exception as e:
        logger.error(f"Error in reply handler: {e}")

@bot.message_handler(commands=['stop'])
def stop_reply_mode(message):
    try:
        user_id = message.from_user.id
        
        # Удаляем команду
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        if user_id in user_reply_mode:
            target_id = user_reply_mode.pop(user_id)
            bot.send_message(user_id, f"✅ Режим ответа отключен для пользователя {format_target_info(target_id)}")
            log_admin_action(message.from_user, f"выключил режим ответа для {format_target_info(target_id)}")
        elif user_id in user_broadcast_mode:
            user_broadcast_mode.pop(user_id)
            bot.send_message(user_id, "✅ Режим рассылки отключен")
        elif user_id in user_support_mode:
            user_support_mode.pop(user_id)
            bot.send_message(user_id, "✅ Режим поддержки отключен")
        else:
            bot.send_message(user_id, "❌ Ни один режим не активен")
    except Exception as e:
        logger.error(f"Error in /stop: {e}")

@bot.message_handler(commands=['ban'])
def ban_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            bot.send_message(user_id, "❌ У вас нет прав для этой команды")
            return
            
        # Удаляем команду админа
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
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
                bot.send_message(user_id, f"✅ Пользователь {format_target_info(target_id)} забанен на {time_str}\nПричина: {reason}")
            else:
                bot.send_message(user_id, f"✅ Пользователь {format_target_info(target_id)} забанен навсегда\nПричина: {reason}")
            try:
                if duration:
                    bot.send_message(target_id, f"🚫 Вы забанены на {time_str}\nПричина: {reason}")
                else:
                    bot.send_message(target_id, f"🚫 Вы забанены навсегда\nПричина: {reason}")
            except:
                pass
            log_admin_action(message.from_user, f"забанил {format_target_info(target_id)}", additional_info=f"время: {duration} сек, причина: {reason}")
        else:
            bot.send_message(user_id, "❌ Ошибка при бане пользователя")
    except Exception as e:
        logger.error(f"Error in /ban: {e}")

@bot.message_handler(commands=['razban'])
def razban_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            bot.send_message(user_id, "❌ У вас нет прав для этой команды")
            return
            
        # Удаляем команду админа
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        args = message.text.split()[1:]
        if len(args) < 1:
            bot.send_message(user_id, "❌ Использование: /razban [id]")
            return
        target_id = int(args[0])
        if unban_user(target_id):
            bot.send_message(user_id, f"✅ Пользователь {format_target_info(target_id)} разбанен")
            try:
                bot.send_message(target_id, "✅ Вы были разбанены")
            except:
                pass
            log_admin_action(message.from_user, f"разбанил {format_target_info(target_id)}")
        else:
            bot.send_message(user_id, "❌ Ошибка при разбане пользователя")
    except Exception as e:
        logger.error(f"Error in /razban: {e}")

@bot.message_handler(commands=['add_promo'])
def add_promo_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            bot.send_message(user_id, "❌ У вас нет прав для этой команды")
            return
            
        # Удаляем команду админа
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
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
            
        # Удаляем команду админа
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
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

@bot.message_handler(commands=['promo'])
def use_promo(message):
    try:
        user_id = message.from_user.id
        ban_info = is_banned(user_id)
        if ban_info:
            bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту команду")
            return
            
        # Удаляем команду пользователя
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
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
            
        # Удаляем команду пользователя
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
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
            
        # Удаляем команду пользователя
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        if ban_info['type'] != 'permanent':
            time_left = format_time_left(ban_info['time_left'])
            bot.send_message(user_id, f"⏳ Вы временно забанены. До разбана осталось: {time_left}")
            return
        if not can_request_unban(user_id):
            bot.send_message(user_id, "❌ Вы можете запрашивать разбан только раз в неделю")
            return
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

@bot.message_handler(commands=['balance'])
def check_balance(message):
    try:
        user_id = message.from_user.id
        ban_info = is_banned(user_id)
        if ban_info:
            bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту команду")
            return
            
        # Удаляем команду пользователя
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        balance = get_user_balance(user_id)
        bot.send_message(user_id, f"💰 Ваш текущий баланс: {balance} монет")
        log_user_action(message.from_user, "check_balance")
    except Exception as e:
        logger.error(f"Error in /balance: {e}")

@bot.message_handler(commands=['top'])
def show_top(message):
    try:
        user_id = message.from_user.id
        ban_info = is_banned(user_id)
        if ban_info:
            bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту команду")
            return
            
        # Удаляем команду пользователя
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        top_users = get_top_users(10)
        if not top_users:
            bot.send_message(user_id, "📊 Пока нет данных о пользователях")
            return
        top_text = "🏆 ТОП-10 ИГРОКОВ ПО БАЛАНСУ 🏆\n\n"
        for i, user in enumerate(top_users, 1):
            top_user_id, username, first_name, last_name, balance = user
            name = f"@{username}" if username else first_name
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            top_text += f"{medal} {name} - {balance:,} монет\n"
        bot.send_message(user_id, top_text)
        log_user_action(message.from_user, "просмотрел топ игроков")
    except Exception as e:
        logger.error(f"Error in /top: {e}")

# Обработчик для пользовательских ставок
@bot.message_handler(func=lambda message: message.from_user.id in user_custom_bet_mode and not message.text.startswith('/'))
def handle_custom_bet(message):
    try:
        user_id = message.from_user.id
        if user_id not in user_custom_bet_mode:
            return
            
        # Удаляем сообщение пользователя
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        try:
            bet_amount = int(message.text)
            balance = get_user_balance(user_id)
            
            if bet_amount < 100:
                bot.send_message(user_id, "❌ Минимальная ставка: 100 монет")
                return
            if bet_amount > balance:
                bot.send_message(user_id, "❌ Недостаточно средств")
                return
                
            user_custom_bet_mode.pop(user_id, None)
            
            # Запускаем игру с кастомной ставкой
            final_result = spin_slots_animation(bot, user_id, message.message_id, bet_amount, user_id)
            all_lines = check_all_lines(final_result)
            total_win, winning_lines = calculate_win(all_lines, bet_amount)
            
            if total_win > 0:
                new_balance = balance - bet_amount + total_win
                update_user_balance(user_id, new_balance)
                result_text = f"🎉 ВЫИГРЫШ\n\n💵 Ставка: {bet_amount}\n💰 Выигрыш: {total_win}\n💎 Баланс: {new_balance}\n\n"
                if winning_lines:
                    result_text += "🏆 Линии:\n" + "\n".join(winning_lines[:3])
            else:
                new_balance = balance - bet_amount
                update_user_balance(user_id, new_balance)
                result_text = f"😞 ПРОИГРЫШ\n\n💵 Ставка: {bet_amount}\n💎 Баланс: {new_balance}"
                
            keyboard = InlineKeyboardMarkup()
            keyboard.add(
                InlineKeyboardButton("🔄 Сыграть еще", callback_data="game_slots"),
                InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
            )
            bot.send_message(user_id, result_text, reply_markup=keyboard)
            log_user_action(message.from_user, f"сыграл в слоты: ставка {bet_amount}, выигрыш {total_win}")
            
        except ValueError:
            bot.send_message(user_id, "❌ Введите корректное число")
            
    except Exception as e:
        logger.error(f"Error in custom bet handler: {e}")

@bot.message_handler(func=lambda message: True)
def handle_unknown_commands(message):
    try:
        user_id = message.from_user.id
        
        # Проверяем, не находится ли пользователь в каком-либо режиме
        if (user_id in user_reply_mode or user_id in user_broadcast_mode or 
            user_id in user_support_mode or user_id in user_custom_bet_mode or 
            user_id in user_blackjack_games):
            return
            
        # Удаляем сообщение если это не команда
        if message.text and message.text.startswith('/'):
            bot.send_message(user_id, 
                           "❌ Неизвестная команда\n"
                           "Используйте /help для просмотра доступных команд")
            log_user_action(message.from_user, f"ввел неизвестную команду: {message.text}")
        else:
            # Удаляем обычное сообщение пользователя
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
                
    except Exception as e:
        logger.error(f"Error in unknown command handler: {e}")

# Глобальная переменная для игр
user_blackjack_games = {}

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
    bot.remove_webhook()
    time.sleep(2)
    bot.set_webhook(url=f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/webhook")
    logger.info("🤖 Webhook configured - bot is ready!")
    if __name__ == "__main__":
        ensure_log_files()
        init_db()
        logger.info("🚀 Starting Flask app directly...")
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
else:
    if __name__ == "__main__":
        ensure_log_files()
        init_db()
        try:
            logger.info("🚀 Starting bot in POLLING mode (local development)")
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            logger.exception("Polling error: %s", e)
