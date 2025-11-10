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

def get_admin_logs(days=30):
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
                        logs.append(line.strip())
                except ValueError:
                    logs.append(line.strip())
            except Exception as e:
                logger.error(f"Error parsing log line: {line} - {e}")
                continue
        logger.info(f"Found {len(logs)} admin logs for period {days} days")
        return logs
    except Exception as e:
        logger.exception("Failed to read admin logs: %s", e)
        return []

def delete_message_with_delay(chat_id, message_id, delay=5):
    """Удаляет сообщение через указанное время"""
    def delete():
        time.sleep(delay)
        try:
            bot.delete_message(chat_id, message_id)
        except:
            pass
    Thread(target=delete, daemon=True).start()

def get_main_user_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🎮 Игры", callback_data="user_games"),
        InlineKeyboardButton("🎫 Промокоды", callback_data="user_promocodes"),
        InlineKeyboardButton("💬 Поддержка", callback_data="user_support"),
        InlineKeyboardButton("🏆 Топ игроков", callback_data="user_top"),
        InlineKeyboardButton("👥 Рефералы", callback_data="user_referrals"),
        InlineKeyboardButton("💰 Баланс", callback_data="user_balance")
    )
    return keyboard

def get_games_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🎰 Слоты", callback_data="game_slots"),
        InlineKeyboardButton("♠️ Blackjack", callback_data="game_blackjack"),
        InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
    )
    return keyboard

def get_promocodes_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📨 Запросить промокод", callback_data="promo_request"),
        InlineKeyboardButton("🎯 Активировать промокод", callback_data="promo_activate"),
        InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
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
        InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
    )
    return keyboard

def get_back_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="user_back_main"))
    return keyboard

def get_main_admin_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("👥 Список пользователей", callback_data="admin_users_list"),
        InlineKeyboardButton("🔍 Найти пользователя", callback_data="admin_users_find"),
        InlineKeyboardButton("📨 Ответить пользователю", callback_data="admin_users_reply"),
        InlineKeyboardButton("🚫 Забанить", callback_data="admin_ban"),
        InlineKeyboardButton("✅ Разбанить", callback_data="admin_razban"),
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("🎫 Создать промокод", callback_data="admin_tools_promo"),
        InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton("📋 Логи", callback_data="admin_stats_logs"),
        InlineKeyboardButton("🗑 Очистить БД", callback_data="admin_clear_db"),
        InlineKeyboardButton("➕ Добавить админа", callback_data="admin_add_admin"),
        InlineKeyboardButton("➖ Удалить админа", callback_data="admin_remove_admin")
    )
    return keyboard

user_last_message_time = {}
MESSAGE_COOLDOWN = 2

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
            
            # ОСНОВНЫЕ ТАБЛИЦЫ
            c.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY, 
                    username TEXT, 
                    first_name TEXT, 
                    last_name TEXT, 
                    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    referrer_id BIGINT,
                    referral_count INTEGER DEFAULT 0
                )
            ''')
            
            # ДОБАВЛЯЕМ КОЛОНКУ ЕСЛИ ЕЁ НЕТ
            try:
                c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT")
            except:
                pass
                
            try:
                c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0")
            except:
                pass
            
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
            c.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    referrer_id BIGINT,
                    referred_id BIGINT PRIMARY KEY,
                    bonus_claimed BOOLEAN DEFAULT FALSE,
                    date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            c.execute("""
                INSERT INTO admins (user_id, username, first_name, is_main_admin) 
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) DO NOTHING
            """, (ADMIN_ID, "werb", "werb", True))
            
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
            
        safe_db_execute(_init)
    except Exception as e:
        logger.exception(f"Failed to initialize DB: {e}")

def register_user(user_id, username, first_name, last_name, referrer_id=None):
    def _register():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, referrer_id) 
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) 
            DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name, last_name = EXCLUDED.last_name
        """, (user_id, username, first_name, last_name, referrer_id))
        c.execute("""
            INSERT INTO user_balance (user_id, balance) 
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id, 0))
        
        # Если есть реферер, добавляем в таблицу referrals и начисляем бонус
        if referrer_id:
            c.execute("""
                INSERT INTO referrals (referrer_id, referred_id) 
                VALUES (%s, %s)
                ON CONFLICT (referred_id) DO NOTHING
            """, (referrer_id, user_id))
            
            # Начисляем бонус рефереру
            c.execute("UPDATE user_balance SET balance = balance + 500 WHERE user_id = %s", (referrer_id,))
            c.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = %s", (referrer_id,))
            
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

def get_user_referrals(user_id):
    def _get_refs():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT u.user_id, u.username, u.first_name, u.date_added 
            FROM users u 
            WHERE u.referrer_id = %s
            ORDER BY u.date_added DESC
        """, (user_id,))
        referrals = c.fetchall()
        conn.close()
        return referrals
    return safe_db_execute(_get_refs)

def get_user_referral_stats(user_id):
    def _get_stats():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT referral_count FROM users WHERE user_id = %s", (user_id,))
        count_result = c.fetchone()
        count = count_result[0] if count_result else 0
        
        c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = %s", (user_id,))
        total_refs = c.fetchone()[0]
        
        conn.close()
        return {
            'referral_count': count,
            'total_refs': total_refs,
            'total_bonus': count * 500
        }
    return safe_db_execute(_get_stats)

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

def clear_all_databases():
    def _clear():
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM promocodes")
        c.execute("DELETE FROM bans")
        c.execute("DELETE FROM referrals")
        c.execute("DELETE FROM user_balance")
        c.execute("DELETE FROM users")
        c.execute("DELETE FROM admins WHERE is_main_admin = FALSE")
        conn.commit()
        conn.close()
        logger.info("All databases cleared by main admin")
        return True
    return safe_db_execute(_clear)

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
    
    # УЛУЧШЕННАЯ АНИМАЦИЯ СЛОТОВ - более долгая и красивая
    animation_steps = 8
    for step in range(animation_steps):
        temp_result = [
            [random.choice(symbols) for _ in range(3)],
            [random.choice(symbols) for _ in range(3)],
            [random.choice(symbols) for _ in range(3)]
        ]
        
        # Добавляем эффект замедления к концу анимации
        if step < animation_steps - 3:
            delay = 0.3
        elif step < animation_steps - 1:
            delay = 0.5
        else:
            delay = 0.7
            
        grid_text = f"{''.join(temp_result[0])}\n{''.join(temp_result[1])}\n{''.join(temp_result[2])}"
        try:
            bot.edit_message_text(
                f"🎰 Крутим...\nСтавка: {bet_amount}\n{grid_text}",
                chat_id=chat_id,
                message_id=message_id
            )
            time.sleep(delay)
        except:
            pass
    
    # Финальный результат
    grid_text = f"{''.join(final_result[0])}\n{''.join(final_result[1])}\n{''.join(final_result[2])}"
    try:
        bot.edit_message_text(
            f"🎰 Результат:\nСтавка: {bet_amount}\n{grid_text}",
            chat_id=chat_id,
            message_id=message_id
        )
    except:
        pass
    
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
user_broadcast_mode = {}
user_support_mode = {}
user_custom_bet_mode = {}
user_find_mode = {}
user_add_admin_mode = {}
user_remove_admin_mode = {}
user_blackjack_games = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        user_id = int(message.from_user.id)
        
        # ПОЛНАЯ ОЧИСТКА ВСЕХ РЕЖИМОВ ДЛЯ ЭТОГО ПОЛЬЗОВАТЕЛЯ
        user_reply_mode.pop(user_id, None)
        user_broadcast_mode.pop(user_id, None)
        user_support_mode.pop(user_id, None)
        user_custom_bet_mode.pop(user_id, None)
        user_find_mode.pop(user_id, None)
        user_add_admin_mode.pop(user_id, None)
        user_remove_admin_mode.pop(user_id, None)
        user_blackjack_games.pop(user_id, None)
        
        # ОЧИСТКА ИСТОРИИ СООБЩЕНИЙ - удаляем все предыдущие сообщения бота
        try:
            # Получаем историю сообщений
            for i in range(message.message_id - 1, max(0, message.message_id - 50), -1):
                try:
                    bot.delete_message(user_id, i)
                except:
                    pass
        except Exception as e:
            logger.debug(f"Could not clear message history: {e}")
        
        # Проверяем бан
        ban_info = is_banned(user_id)
        if ban_info:
            if ban_info['type'] == 'permanent':
                bot.send_message(user_id, "🚫 Вы забанены навсегда. Для разбана используйте /unban")
            else:
                time_left = format_time_left(ban_info['time_left'])
                bot.send_message(user_id, f"🚫 Вы забанены. До разбана осталось: {time_left}")
            return
        
        # Проверяем реферальную ссылку
        args = message.text.split()
        referrer_id = None
        if len(args) > 1:
            try:
                referrer_id = int(args[1])
                if referrer_id == user_id:
                    referrer_id = None
            except:
                referrer_id = None
        
        register_user(user_id,
                      message.from_user.username,
                      message.from_user.first_name,
                      message.from_user.last_name,
                      referrer_id)
        
        balance = get_user_balance(user_id)
        
        # СОЗДАЕМ ПРИВЕТСТВЕННОЕ СООБЩЕНИЕ С КНОПКАМИ
        if is_admin(user_id):
            # Для админов
            welcome_text = f"🛠 Добро пожаловать в АДМИН ПАНЕЛЬ!\n\n💰 Баланс: {balance} монет"
            markup = get_main_admin_keyboard()
        else:
            # Для пользователей
            welcome_text = f"🎉 Добро пожаловать в WERB HUB!\n\n💰 Баланс: {balance} монет"
            
            # Добавляем реферальную информацию если был реферер
            if referrer_id:
                welcome_text += f"\n\n🎁 Вы пришли по реферальной ссылке! Получено 500 монет"
            
            # Добавляем реферальную ссылку
            ref_link = f"https://t.me/{bot.get_me().username}?start={user_id}"
            welcome_text += f"\n\n👥 Приглашайте друзей и получайте 500 монет за каждого!\nВаша ссылка:\n`{ref_link}`"
            
            markup = get_main_user_keyboard()
        
        # УДАЛЯЕМ КОМАНДУ /start
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
        
        # ОТПРАВЛЯЕМ НОВОЕ ПРИВЕТСТВЕННОЕ СООБЩЕНИЕ
        sent_msg = bot.send_message(
            user_id, 
            welcome_text,
            parse_mode='Markdown',
            reply_markup=markup
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
                "🎮 Выберите игру:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_games_keyboard()
            )
        elif call.data == 'user_promocodes':
            bot.edit_message_text(
                "🎫 Промокоды:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_promocodes_keyboard()
            )
        elif call.data == 'user_support':
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
                top_text = "🏆 ТОП-10 ИГРОКОВ\n\n"
                for i, user in enumerate(top_users, 1):
                    top_user_id, username, first_name, last_name, balance = user
                    name = f"@{username}" if username else first_name
                    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                    top_text += f"{medal} {name} - {balance:,} монет\n"
                keyboard = InlineKeyboardMarkup()
                keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="user_back_main"))
                bot.edit_message_text(
                    top_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Error getting top users: {e}")
                bot.edit_message_text(
                    "❌ Ошибка при получении топа игроков",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_back_keyboard()
                )
        elif call.data == 'user_referrals':
            try:
                stats = get_user_referral_stats(user_id)
                ref_link = f"https://t.me/{bot.get_me().username}?start={user_id}"
                ref_text = f"👥 Реферальная система\n\n"
                ref_text += f"🔗 Ваша ссылка:\n`{ref_link}`\n\n"
                ref_text += f"📊 Статистика:\n"
                ref_text += f"• Приглашено друзей: {stats['total_refs']}\n"
                ref_text += f"• Получено бонусов: {stats['total_bonus']} монет\n"
                ref_text += f"• За каждого друга: 500 монет\n\n"
                ref_text += f"💡 Отправляйте ссылку друзьям и получайте бонусы!"
                
                keyboard = InlineKeyboardMarkup()
                keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="user_back_main"))
                bot.edit_message_text(
                    ref_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode='Markdown',
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Error getting referral stats: {e}")
                bot.edit_message_text(
                    "❌ Ошибка при получении статистики",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_back_keyboard()
                )
        elif call.data == 'user_balance':
            bot.edit_message_text(
                f"💰 Баланс: {balance} монет",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_back_keyboard()
            )
        elif call.data == 'user_back_main':
            user_support_mode.pop(user_id, None)
            user_custom_bet_mode.pop(user_id, None)
            
            balance = get_user_balance(user_id)
            welcome_text = f"🎉 Добро пожаловать в WERB HUB!\n\n💰 Баланс: {balance} монет"
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
                f"🎰 Слоты\n💰 Баланс: {balance} монет\n\nВыберите ставку:\nМин: 100 монет\nМакс: {balance}",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_bet_keyboard_inline()
            )
        elif call.data == 'game_blackjack':
            if balance < 100:
                bot.answer_callback_query(call.id, "❌ Минимум 100 монет для игры")
                return
            bot.edit_message_text(
                f"♠️ Blackjack\n💰 Баланс: {balance} монет\n\nВыберите ставку:\nМин: 100 монет\nМакс: {balance}",
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
                    "✅ Запрос отправлен администраторам\nОжидайте создания промокода",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_back_keyboard()
                )
                log_user_action(call.from_user, "request_promo")
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка при отправке запроса")
        elif call.data == 'promo_activate':
            bot.edit_message_text(
                "🎯 Введите промокод:\n/promo КОД",
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
                
            if call.message.text.startswith("🎰 Слоты"):
                final_result = spin_slots_animation(bot, call.message.chat.id, call.message.message_id, bet_amount, user_id)
                all_lines = check_all_lines(final_result)
                total_win, winning_lines = calculate_win(all_lines, bet_amount)
                
                if total_win > 0:
                    new_balance = balance - bet_amount + total_win
                    update_user_balance(user_id, new_balance)
                    result_text = f"🎉 ВЫИГРЫШ!\n\n💵 Ставка: {bet_amount}\n💰 Выигрыш: {total_win}\n💎 Баланс: {new_balance}\n\n"
                    if winning_lines:
                        result_text += "🏆 Выигрышные линии:\n" + "\n".join(winning_lines[:3])
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
                
            elif call.message.text.startswith("♠️ Blackjack"):
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
                game_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'], hide_dealer=True)}\n\n💵 Ставка: {bet_amount}"
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
            
            if call.message.text.startswith("🎰 Слоты"):
                final_result = spin_slots_animation(bot, call.message.chat.id, call.message.message_id, bet_amount, user_id)
                all_lines = check_all_lines(final_result)
                total_win, winning_lines = calculate_win(all_lines, bet_amount)
                
                if total_win > 0:
                    new_balance = balance - bet_amount + total_win
                    update_user_balance(user_id, new_balance)
                    result_text = f"🎉 ВЫИГРЫШ!\n\n💵 Ставка: {bet_amount}\n💰 Выигрыш: {total_win}\n💎 Баланс: {new_balance}\n\n"
                    if winning_lines:
                        result_text += "🏆 Выигрышные линии:\n" + "\n".join(winning_lines[:3])
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
                
            elif call.message.text.startswith("♠️ Blackjack"):
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
                game_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'], hide_dealer=True)}\n\n💵 Ставка: {bet_amount}"
                bot.edit_message_text(
                    game_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_blackjack_keyboard()
                )
                
        elif call.data == 'bet_custom':
            user_custom_bet_mode[user_id] = True
            bot.edit_message_text(
                f"💰 Введите свою ставку (число):\n\nМин: 100 монет\nМакс: {balance}",
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
                result_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value}) - ПЕРЕБОР!\n🎴 Рука дилера: {format_hand(game['dealer_hand'])} ({calculate_hand_value(game['dealer_hand'])})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n😞 Вы проиграли!"
                bot.edit_message_text(
                    result_text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_blackjack_keyboard("finished")
                )
                del user_blackjack_games[user_id]
                log_user_action(call.from_user, f"сыграл в blackjack: ставка {game['bet']}, проигрыш")
            else:
                game_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'], hide_dealer=True)}\n\n💵 Ставка: {game['bet']}"
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
                result_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💰 Выигрыш: {win_amount}\n💎 Баланс: {new_balance}\n\n🎉 Вы выиграли!"
            elif player_value == dealer_value:
                new_balance = balance
                result_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n🤝 Ничья!"
            else:
                new_balance = balance - game['bet']
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n😞 Вы проиграли!"
                
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
                result_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value}) - ПЕРЕБОР!\n🎴 Рука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n😞 Вы проиграли!"
            elif dealer_value > 21 or player_value > dealer_value:
                win_amount = game['bet'] * 2
                new_balance = balance - game['bet'] + win_amount
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💰 Выигрыш: {win_amount}\n💎 Баланс: {new_balance}\n\n🎉 Вы выиграли!"
            elif player_value == dealer_value:
                new_balance = balance
                result_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n🤝 Ничья!"
            else:
                new_balance = balance - game['bet']
                update_user_balance(user_id, new_balance)
                result_text = f"♠️ Blackjack ♠️\n\n🎴 Ваша рука: {format_hand(game['player_hand'])} ({player_value})\n🎴 Рука дилера: {format_hand(game['dealer_hand'])} ({dealer_value})\n\n💵 Ставка: {game['bet']}\n💎 Баланс: {new_balance}\n\n😞 Вы проиграли!"
                
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
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        bot.send_message(
            user_id,
            "🛠 АДМИН ПАНЕЛЬ\n\nВыберите действие:",
            reply_markup=get_main_admin_keyboard()
        )
        log_admin_action(message.from_user, "открыл админ панель")
    except Exception as e:
        logger.error(f"Error in /admin: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def handle_admin_callbacks(call):
    user_id = call.from_user.id
    if not is_admin(user_id):
        bot.answer_callback_query(call.id, "❌ Нет прав доступа")
        return
        
    try:
        if call.data == 'admin_users_list':
            users = get_all_users()
            users_text = f"👥 Всего пользователей: {len(users)}\n\n"
            for i, user in enumerate(users[:15], 1):
                user_id, username, first_name, last_name = user
                name = f"{first_name} {last_name}" if last_name else first_name
                users_text += f"{i}. {name} (@{username}) - ID: {user_id}\n"
            if len(users) > 15:
                users_text += f"\n... и еще {len(users) - 15} пользователей"
                
            bot.edit_message_text(
                users_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_admin_keyboard()
            )
            
        elif call.data == 'admin_users_find':
            user_find_mode[user_id] = True
            bot.edit_message_text(
                "🔍 Поиск пользователя\n\nВведите ID пользователя:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="admin_back"))
            )
            
        elif call.data == 'admin_users_reply':
            user_reply_mode[user_id] = True
            bot.edit_message_text(
                "💬 Режим ответа\n\nВведите ID пользователя для ответа:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="admin_back"))
            )
            
        elif call.data == 'admin_ban':
            bot.edit_message_text(
                "🚫 Бан пользователя\n\nИспользуйте команду:\n/ban ID время_секунд причина\n\nПример:\n/ban 1234567 3600 Спам",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_admin_keyboard()
            )
            
        elif call.data == 'admin_razban':
            bot.edit_message_text(
                "✅ Разбан пользователя\n\nИспользуйте команду:\n/razban ID\n\nПример:\n/razban 1234567",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_admin_keyboard()
            )
            
        elif call.data == 'admin_stats':
            user_count = get_user_count()
            promo_stats = get_promocode_stats()
            stats_text = f"📊 СТАТИСТИКА\n\n"
            stats_text += f"👥 Пользователей: {user_count}\n"
            stats_text += f"🎫 Промокодов: {promo_stats['total']}\n"
            stats_text += f"✅ Использовано: {promo_stats['used']}\n"
            stats_text += f"🆓 Доступно: {promo_stats['available']}"
            
            bot.edit_message_text(
                stats_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_admin_keyboard()
            )
            
        elif call.data == 'admin_tools_promo':
            bot.edit_message_text(
                "🎫 Создание промокода\n\nИспользуйте команду:\n/add_promo КОД СУММА\n\nПример:\n/add_promo SUMMER2024 1000",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_admin_keyboard()
            )
            
        elif call.data == 'admin_broadcast':
            user_broadcast_mode[user_id] = True
            bot.edit_message_text(
                "📢 РАССЫЛКА\n\nВведите сообщение для рассылки всем пользователям:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="admin_back"))
            )
            
        elif call.data == 'admin_stats_logs':
            logs = get_admin_logs(days=7)
            logs_text = f"📋 Логи за 7 дней: {len(logs)} записей\n\n"
            for log in logs[:10]:
                logs_text += f"• {log}\n\n"
            if len(logs) > 10:
                logs_text += f"... и еще {len(logs) - 10} записей"
                
            bot.edit_message_text(
                logs_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_admin_keyboard()
            )
            
        elif call.data == 'admin_clear_db':
            if not is_main_admin(user_id):
                bot.answer_callback_query(call.id, "❌ Только для главного админа")
                return
                
            if clear_all_databases():
                bot.edit_message_text(
                    "✅ Все базы данных очищены!",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_main_admin_keyboard()
                )
                log_admin_action(call.from_user, "очистил все базы данных")
            else:
                bot.edit_message_text(
                    "❌ Ошибка при очистке БД",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=get_main_admin_keyboard()
                )
                
        elif call.data == 'admin_add_admin':
            user_add_admin_mode[user_id] = True
            bot.edit_message_text(
                "➕ Добавление админа\n\nВведите ID пользователя:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="admin_back"))
            )
            
        elif call.data == 'admin_remove_admin':
            user_remove_admin_mode[user_id] = True
            bot.edit_message_text(
                "➖ Удаление админа\n\nВведите ID админа:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="admin_back"))
            )
            
        elif call.data == 'admin_back':
            # Очищаем все режимы
            user_reply_mode.pop(user_id, None)
            user_broadcast_mode.pop(user_id, None)
            user_find_mode.pop(user_id, None)
            user_add_admin_mode.pop(user_id, None)
            user_remove_admin_mode.pop(user_id, None)
            
            bot.edit_message_text(
                "🛠 АДМИН ПАНЕЛЬ\n\nВыберите действие:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=get_main_admin_keyboard()
            )
            
    except Exception as e:
        logger.error(f"Error in admin callback: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")

# Обработчики режимов админа
@bot.message_handler(func=lambda message: message.from_user.id in user_reply_mode and not message.text.startswith('/'))
def handle_admin_reply_mode(message):
    try:
        admin_id = message.from_user.id
        if admin_id not in user_reply_mode:
            return
            
        target_id = message.text.strip()
        try:
            target_id = int(target_id)
            user_reply_mode[admin_id] = target_id
            
            # Удаляем сообщение с ID
            try:
                bot.delete_message(admin_id, message.message_id)
            except:
                pass
                
            bot.send_message(
                admin_id,
                f"💬 Режим ответа для пользователя {target_id}\n\nОтправьте сообщение (текст, фото, видео и т.д.):\n\n/stop - выйти из режима"
            )
        except:
            msg = bot.send_message(admin_id, "❌ Неверный ID пользователя")
            delete_message_with_delay(admin_id, msg.message_id, 3)
            delete_message_with_delay(admin_id, message.message_id, 3)
            
    except Exception as e:
        logger.error(f"Error in admin reply mode: {e}")

@bot.message_handler(func=lambda message: message.from_user.id in user_broadcast_mode and not message.text.startswith('/'))
def handle_broadcast_message(message):
    try:
        admin_id = message.from_user.id
        if admin_id not in user_broadcast_mode:
            return
            
        users = get_all_users()
        success_count = 0
        fail_count = 0
        
        progress_msg = bot.send_message(admin_id, f"📢 Начинаем рассылку для {len(users)} пользователей...")
        
        for user in users:
            try:
                user_id = user[0]
                if message.content_type == 'text':
                    bot.send_message(user_id, message.text)  # БЕЗ "Рассылка от администратора"
                elif message.content_type == 'photo':
                    bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption)
                elif message.content_type == 'video':
                    bot.send_video(user_id, message.video.file_id, caption=message.caption)
                elif message.content_type == 'document':
                    bot.send_document(user_id, message.document.file_id, caption=message.caption)
                elif message.content_type == 'audio':
                    bot.send_audio(user_id, message.audio.file_id, caption=message.caption)
                elif message.content_type == 'voice':
                    bot.send_voice(user_id, message.voice.file_id)
                success_count += 1
            except Exception as e:
                fail_count += 1
            
            if (success_count + fail_count) % 10 == 0:
                try:
                    bot.edit_message_text(
                        f"📢 Рассылка...\n✅ Успешно: {success_count}\n❌ Ошибок: {fail_count}",
                        chat_id=admin_id,
                        message_id=progress_msg.message_id
                    )
                except:
                    pass
        
        user_broadcast_mode.pop(admin_id, None)
        
        # Удаляем прогресс и исходное сообщение
        try:
            bot.delete_message(admin_id, progress_msg.message_id)
        except:
            pass
        try:
            bot.delete_message(admin_id, message.message_id)
        except:
            pass
            
        result_msg = bot.send_message(
            admin_id,
            f"✅ Рассылка завершена!\n\n📊 Результаты:\n✅ Успешно: {success_count}\n❌ Ошибок: {fail_count}",
            reply_markup=get_main_admin_keyboard()
        )
        delete_message_with_delay(admin_id, result_msg.message_id, 5)
        
        log_admin_action(message.from_user, f"сделал рассылку: успешно {success_count}, ошибок {fail_count}")
        
    except Exception as e:
        logger.error(f"Error in broadcast handler: {e}")

@bot.message_handler(func=lambda message: message.from_user.id in user_support_mode and not message.text.startswith('/'))
def handle_support_message(message):
    try:
        user_id = message.from_user.id
        if user_id not in user_support_mode:
            return
            
        admins = get_all_admins()
        user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        
        # Удаляем сообщение пользователя
        try:
            bot.delete_message(user_id, message.message_id)
        except:
            pass
            
        for admin in admins:
            try:
                admin_id = admin[0]
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("📨 Ответить", callback_data=f"reply_{user_id}"))
                
                if message.content_type == 'text':
                    bot.send_message(admin_id, 
                                   f"💬 Поддержка от {user_info} (ID: {user_id}):\n\n{message.text}",
                                   reply_markup=markup)
                else:
                    caption = f"💬 Поддержка от {user_info} (ID: {user_id})"
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
                logger.error(f"Failed to forward to admin {admin[0]}: {e}")
        
        confirm_msg = bot.send_message(user_id, "✅ Ваше сообщение отправлено в поддержку! Ожидайте ответа.")
        delete_message_with_delay(user_id, confirm_msg.message_id, 5)
        
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
        
        bot.answer_callback_query(call.id, "💬 Режим ответа включен")
        
        # Удаляем старое сообщение с кнопкой
        try:
            bot.delete_message(admin_id, call.message.message_id)
        except:
            pass
            
        bot.send_message(
            admin_id,
            f"💬 Режим ответа для пользователя {target_id}\n\nОтправьте сообщение (текст, фото, видео и т.д.):\n\n/stop - выйти из режима"
        )
        log_admin_action(call.from_user, f"включил режим ответа для {target_id}")
    except Exception as e:
        logger.error(f"Error in reply callback: {e}")

# Обработчик ответов админа пользователям
@bot.message_handler(func=lambda message: message.from_user.id in user_reply_mode and isinstance(user_reply_mode[message.from_user.id], int))
def handle_admin_reply(message):
    try:
        admin_id = message.from_user.id
        target_id = user_reply_mode[admin_id]
        
        # ЕСЛИ ЭТО КОМАНДА /stop - НЕ ОТПРАВЛЯЕМ ЕЕ ПОЛЬЗОВАТЕЛЮ
        if message.text and message.text.startswith('/stop'):
            user_reply_mode.pop(admin_id, None)
            msg = bot.send_message(admin_id, f"✅ Режим ответа отключен для пользователя {target_id}")
            delete_message_with_delay(admin_id, msg.message_id, 3)
            delete_message_with_delay(admin_id, message.message_id, 3)
            return
            
        # Удаляем сообщение админа
        try:
            bot.delete_message(admin_id, message.message_id)
        except:
            pass
            
        try:
            if message.content_type == 'text':
                bot.send_message(target_id, message.text)  # БЕЗ "Ответ от администратора"
                confirm_msg = bot.send_message(admin_id, f"✅ Ответ отправлен пользователю {target_id}")
                delete_message_with_delay(admin_id, confirm_msg.message_id, 3)
                log_admin_action(message.from_user, f"ответил {target_id}", additional_info=f"текст: {message.text}")
            else:
                if message.content_type == 'photo':
                    bot.send_photo(target_id, message.photo[-1].file_id, caption=message.caption)
                    media_type = "фото"
                elif message.content_type == 'video':
                    bot.send_video(target_id, message.video.file_id, caption=message.caption)
                    media_type = "видео"
                elif message.content_type == 'document':
                    bot.send_document(target_id, message.document.file_id, caption=message.caption)
                    media_type = "документ"
                elif message.content_type == 'audio':
                    bot.send_audio(target_id, message.audio.file_id, caption=message.caption)
                    media_type = "аудио"
                elif message.content_type == 'voice':
                    bot.send_voice(target_id, message.voice.file_id)
                    media_type = "голосовое сообщение"
                else:
                    media_type = "медиа"
                    
                confirm_msg = bot.send_message(admin_id, f"✅ {media_type.capitalize()}-ответ отправлен пользователю {target_id}")
                delete_message_with_delay(admin_id, confirm_msg.message_id, 3)
                log_admin_action(message.from_user, f"ответил {target_id}", additional_info=f"[{media_type}]")
                
        except Exception as e:
            error_msg = bot.send_message(admin_id, f"❌ Не удалось отправить сообщение пользователю {target_id}")
            delete_message_with_delay(admin_id, error_msg.message_id, 5)
            logger.error(f"Failed to send reply to {target_id}: {e}")
    except Exception as e:
        logger.error(f"Error in reply handler: {e}")

# Команды админов
@bot.message_handler(commands=['ban'])
def ban_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        args = message.text.split()[1:]
        if len(args) < 1:
            bot.send_message(user_id, "❌ Использование: /ban ID время_секунд причина")
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
                result_msg = bot.send_message(user_id, f"✅ Пользователь {target_id} забанен на {time_str}\nПричина: {reason}")
            else:
                result_msg = bot.send_message(user_id, f"✅ Пользователь {target_id} забанен навсегда\nПричина: {reason}")
            
            delete_message_with_delay(user_id, result_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
            
            try:
                if duration:
                    bot.send_message(target_id, f"🚫 Вы забанены на {time_str}\nПричина: {reason}")
                else:
                    bot.send_message(target_id, f"🚫 Вы забанены навсегда\nПричина: {reason}")
            except:
                pass
            log_admin_action(message.from_user, f"забанил {target_id}", additional_info=f"время: {duration} сек, причина: {reason}")
        else:
            error_msg = bot.send_message(user_id, "❌ Ошибка при бане пользователя")
            delete_message_with_delay(user_id, error_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
    except Exception as e:
        logger.error(f"Error in /ban: {e}")

@bot.message_handler(commands=['razban'])
def razban_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        args = message.text.split()[1:]
        if len(args) < 1:
            bot.send_message(user_id, "❌ Использование: /razban ID")
            return
            
        target_id = int(args[0])
        if unban_user(target_id):
            result_msg = bot.send_message(user_id, f"✅ Пользователь {target_id} разбанен")
            delete_message_with_delay(user_id, result_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
            
            try:
                bot.send_message(target_id, "✅ Вы были разбанены")
            except:
                pass
            log_admin_action(message.from_user, f"разбанил {target_id}")
        else:
            error_msg = bot.send_message(user_id, "❌ Ошибка при разбане пользователя")
            delete_message_with_delay(user_id, error_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
    except Exception as e:
        logger.error(f"Error in /razban: {e}")

@bot.message_handler(commands=['add_promo'])
def add_promo_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        args = message.text.split()[1:]
        if len(args) < 2:
            bot.send_message(user_id, "❌ Использование: /add_promo КОД СУММА")
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
            result_msg = bot.send_message(user_id, f"✅ Промокод {promocode} на {value} монет создан!")
            delete_message_with_delay(user_id, result_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
            log_admin_action(message.from_user, f"создал промокод {promocode} на {value} монет")
        else:
            error_msg = bot.send_message(user_id, "❌ Ошибка при создании промокода")
            delete_message_with_delay(user_id, error_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
    except Exception as e:
        logger.error(f"Error in /add_promo: {e}")

@bot.message_handler(commands=['adminlogs'])
def admin_logs_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
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
            
        logs_text = f"📋 Логи за {days} дней: {len(logs)} записей\n\n"
        for log in logs[:15]:
            logs_text += f"• {log}\n\n"
        if len(logs) > 15:
            logs_text += f"... и еще {len(logs) - 15} записей"
            
        bot.send_message(user_id, logs_text)
        log_admin_action(message.from_user, f"просмотрел логи за {days} дней")
    except Exception as e:
        logger.error(f"Error in /adminlogs: {e}")

@bot.message_handler(commands=['stats'])
def stats_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        user_count = get_user_count()
        promo_stats = get_promocode_stats()
        stats_text = f"📊 СТАТИСТИКА БОТА\n\n"
        stats_text += f"👥 Пользователей: {user_count}\n"
        stats_text += f"🎫 Промокодов всего: {promo_stats['total']}\n"
        stats_text += f"✅ Использовано: {promo_stats['used']}\n"
        stats_text += f"🆓 Доступно: {promo_stats['available']}"
        
        bot.send_message(user_id, stats_text)
        log_admin_action(message.from_user, "просмотрел статистику")
    except Exception as e:
        logger.error(f"Error in /stats: {e}")

@bot.message_handler(commands=['add_admin'])
def add_admin_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        args = message.text.split()[1:]
        if len(args) < 1:
            bot.send_message(user_id, "❌ Использование: /add_admin ID")
            return
            
        try:
            new_admin_id = int(args[0])
            # Здесь нужно получить информацию о пользователе, но для простоты используем заглушки
            if add_admin(new_admin_id, "unknown", "User"):
                result_msg = bot.send_message(user_id, f"✅ Пользователь {new_admin_id} добавлен в админы")
                delete_message_with_delay(user_id, result_msg.message_id, 5)
                delete_message_with_delay(user_id, message.message_id, 5)
                log_admin_action(message.from_user, f"добавил админа {new_admin_id}")
            else:
                error_msg = bot.send_message(user_id, "❌ Ошибка при добавлении админа")
                delete_message_with_delay(user_id, error_msg.message_id, 5)
                delete_message_with_delay(user_id, message.message_id, 5)
        except ValueError:
            error_msg = bot.send_message(user_id, "❌ ID должен быть числом")
            delete_message_with_delay(user_id, error_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
    except Exception as e:
        logger.error(f"Error in /add_admin: {e}")

@bot.message_handler(commands=['remove_admin'])
def remove_admin_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        args = message.text.split()[1:]
        if len(args) < 1:
            bot.send_message(user_id, "❌ Использование: /remove_admin ID")
            return
            
        try:
            admin_id = int(args[0])
            if remove_admin(admin_id):
                result_msg = bot.send_message(user_id, f"✅ Админ {admin_id} удален")
                delete_message_with_delay(user_id, result_msg.message_id, 5)
                delete_message_with_delay(user_id, message.message_id, 5)
                log_admin_action(message.from_user, f"удалил админа {admin_id}")
            else:
                error_msg = bot.send_message(user_id, "❌ Ошибка при удалении админа")
                delete_message_with_delay(user_id, error_msg.message_id, 5)
                delete_message_with_delay(user_id, message.message_id, 5)
        except ValueError:
            error_msg = bot.send_message(user_id, "❌ ID должен быть числом")
            delete_message_with_delay(user_id, error_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
    except Exception as e:
        logger.error(f"Error in /remove_admin: {e}")

@bot.message_handler(commands=['clear_db'])
def clear_db_command(message):
    try:
        user_id = message.from_user.id
        if not is_main_admin(user_id):
            bot.send_message(user_id, "❌ Только для главного админа")
            return
            
        if clear_all_databases():
            result_msg = bot.send_message(user_id, "✅ Все базы данных очищены!")
            delete_message_with_delay(user_id, result_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
            log_admin_action(message.from_user, "очистил все базы данных")
        else:
            error_msg = bot.send_message(user_id, "❌ Ошибка при очистке БД")
            delete_message_with_delay(user_id, error_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
    except Exception as e:
        logger.error(f"Error in /clear_db: {e}")

@bot.message_handler(commands=['broadcast'])
def broadcast_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        user_broadcast_mode[user_id] = True
        msg = bot.send_message(user_id, "📢 Режим рассылки\n\nВведите сообщение для рассылки:")
        delete_message_with_delay(user_id, msg.message_id, 10)
        delete_message_with_delay(user_id, message.message_id, 5)
    except Exception as e:
        logger.error(f"Error in /broadcast: {e}")

@bot.message_handler(commands=['reply'])
def reply_command(message):
    try:
        user_id = message.from_user.id
        if not is_admin(user_id):
            # УДАЛЯЕМ КОМАНДУ ЕСЛИ ПОЛЬЗОВАТЕЛЬ НЕ АДМИН
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
            return
            
        args = message.text.split()[1:]
        if len(args) < 1:
            bot.send_message(user_id, "❌ Использование: /reply ID")
            return
            
        try:
            target_id = int(args[0])
            user_reply_mode[user_id] = target_id
            msg = bot.send_message(user_id, f"💬 Режим ответа для пользователя {target_id}\n\nОтправьте сообщение:")
            delete_message_with_delay(user_id, msg.message_id, 10)
            delete_message_with_delay(user_id, message.message_id, 5)
        except ValueError:
            error_msg = bot.send_message(user_id, "❌ ID должен быть числом")
            delete_message_with_delay(user_id, error_msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
    except Exception as e:
        logger.error(f"Error in /reply: {e}")

@bot.message_handler(commands=['stop'])
def stop_command(message):
    try:
        user_id = message.from_user.id
        
        if user_id in user_reply_mode:
            target_id = user_reply_mode.pop(user_id)
            msg = bot.send_message(user_id, f"✅ Режим ответа отключен для пользователя {target_id}")
            delete_message_with_delay(user_id, msg.message_id, 3)
        elif user_id in user_broadcast_mode:
            user_broadcast_mode.pop(user_id)
            msg = bot.send_message(user_id, "✅ Режим рассылки отключен")
            delete_message_with_delay(user_id, msg.message_id, 3)
        elif user_id in user_support_mode:
            user_support_mode.pop(user_id)
            msg = bot.send_message(user_id, "✅ Режим поддержки отключен")
            delete_message_with_delay(user_id, msg.message_id, 3)
        elif user_id in user_find_mode:
            user_find_mode.pop(user_id)
            msg = bot.send_message(user_id, "✅ Режим поиска отключен")
            delete_message_with_delay(user_id, msg.message_id, 3)
        elif user_id in user_add_admin_mode:
            user_add_admin_mode.pop(user_id)
            msg = bot.send_message(user_id, "✅ Режим добавления админа отключен")
            delete_message_with_delay(user_id, msg.message_id, 3)
        elif user_id in user_remove_admin_mode:
            user_remove_admin_mode.pop(user_id)
            msg = bot.send_message(user_id, "✅ Режим удаления админа отключен")
            delete_message_with_delay(user_id, msg.message_id, 3)
        else:
            msg = bot.send_message(user_id, "❌ Ни один режим не активен")
            delete_message_with_delay(user_id, msg.message_id, 3)
            
        delete_message_with_delay(user_id, message.message_id, 3)
    except Exception as e:
        logger.error(f"Error in /stop: {e}")

# Остальные команды для пользователей
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
            bot.send_message(user_id, "❌ Использование: /promo КОД")
            return
            
        promocode = args[1]
        value, result_message = use_promocode(promocode, user_id)
        if value is not None:
            msg = bot.send_message(user_id, result_message)
            delete_message_with_delay(user_id, msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
            log_user_action(message.from_user, f"used_promo {promocode}")
        else:
            msg = bot.send_message(user_id, f"❌ {result_message}")
            delete_message_with_delay(user_id, msg.message_id, 5)
            delete_message_with_delay(user_id, message.message_id, 5)
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
            
        admins = get_all_admins()
        user_info = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        for admin in admins:
            try:
                admin_id = admin[0]
                bot.send_message(admin_id, f"🎫 Пользователь {user_info} (ID: {user_id}) запросил промокод")
            except Exception as e:
                logger.error(f"Failed to notify admin {admin[0]} about promo request: {e}")
        msg = bot.send_message(user_id, "✅ Ваш запрос на промокод отправлен администраторам. Ожидайте создания промокода.")
        delete_message_with_delay(user_id, msg.message_id, 5)
        delete_message_with_delay(user_id, message.message_id, 5)
        log_user_action(message.from_user, "request_promo")
    except Exception as e:
        logger.error(f"Error in /get_promo: {e}")

@bot.message_handler(commands=['balance'])
def check_balance(message):
    try:
        user_id = message.from_user.id
        ban_info = is_banned(user_id)
        if ban_info:
            bot.send_message(user_id, "🚫 Вы забанены и не можете использовать эту команду")
            return
            
        balance = get_user_balance(user_id)
        msg = bot.send_message(user_id, f"💰 Ваш текущий баланс: {balance} монет")
        delete_message_with_delay(user_id, msg.message_id, 5)
        delete_message_with_delay(user_id, message.message_id, 5)
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

# Обработчик кастомных ставок
@bot.message_handler(func=lambda message: message.from_user.id in user_custom_bet_mode and not message.text.startswith('/'))
def handle_custom_bet(message):
    try:
        user_id = message.from_user.id
        if user_id not in user_custom_bet_mode:
            return
            
        try:
            bet_amount = int(message.text)
            balance = get_user_balance(user_id)
            
            if bet_amount < 100:
                msg = bot.send_message(user_id, "❌ Минимальная ставка: 100 монет")
                delete_message_with_delay(user_id, msg.message_id, 3)
                delete_message_with_delay(user_id, message.message_id, 3)
                return
            if bet_amount > balance:
                msg = bot.send_message(user_id, "❌ Недостаточно средств")
                delete_message_with_delay(user_id, msg.message_id, 3)
                delete_message_with_delay(user_id, message.message_id, 3)
                return
                
            user_custom_bet_mode.pop(user_id, None)
            
            # Запускаем игру с кастомной ставкой - РЕДАКТИРУЕМ СУЩЕСТВУЮЩЕЕ СООБЩЕНИЕ
            final_result = spin_slots_animation(bot, user_id, message.message_id, bet_amount, user_id)
            all_lines = check_all_lines(final_result)
            total_win, winning_lines = calculate_win(all_lines, bet_amount)
            
            if total_win > 0:
                new_balance = balance - bet_amount + total_win
                update_user_balance(user_id, new_balance)
                result_text = f"🎉 ВЫИГРЫШ!\n\n💵 Ставка: {bet_amount}\n💰 Выигрыш: {total_win}\n💎 Баланс: {new_balance}\n\n"
                if winning_lines:
                    result_text += "🏆 Выигрышные линии:\n" + "\n".join(winning_lines[:3])
            else:
                new_balance = balance - bet_amount
                update_user_balance(user_id, new_balance)
                result_text = f"😞 ПРОИГРЫШ\n\n💵 Ставка: {bet_amount}\n💎 Баланс: {new_balance}"
                
            keyboard = InlineKeyboardMarkup()
            keyboard.add(
                InlineKeyboardButton("🔄 Сыграть еще", callback_data="game_slots"),
                InlineKeyboardButton("🔙 Назад", callback_data="user_back_main")
            )
            
            # РЕДАКТИРУЕМ СУЩЕСТВУЮЩЕЕ СООБЩЕНИЕ ВМЕСТО СОЗДАНИЯ НОВОГО
            try:
                bot.edit_message_text(
                    result_text,
                    chat_id=user_id,
                    message_id=message.message_id,
                    reply_markup=keyboard
                )
            except:
                # Если не удалось отредактировать, отправляем новое
                bot.send_message(user_id, result_text, reply_markup=keyboard)
                
            log_user_action(message.from_user, f"сыграл в слоты: ставка {bet_amount}, выигрыш {total_win}")
            
        except ValueError:
            msg = bot.send_message(user_id, "❌ Введите корректное число")
            delete_message_with_delay(user_id, msg.message_id, 3)
            delete_message_with_delay(user_id, message.message_id, 3)
            
    except Exception as e:
        logger.error(f"Error in custom bet handler: {e}")

@bot.message_handler(func=lambda message: True)
def handle_unknown_commands(message):
    try:
        user_id = message.from_user.id
        
        # Проверяем, не находится ли пользователь в каком-либо режиме
        if (user_id in user_reply_mode or user_id in user_broadcast_mode or 
            user_id in user_support_mode or user_id in user_custom_bet_mode or 
            user_id in user_blackjack_games or user_id in user_find_mode or
            user_id in user_add_admin_mode or user_id in user_remove_admin_mode):
            return
            
        # ЕСЛИ ЭТО КОМАНДА (начинается с /) - удаляем и показываем сообщение
        if message.text and message.text.startswith('/'):
            # УДАЛЯЕМ КОМАНДУ
            try:
                bot.delete_message(user_id, message.message_id)
            except:
                pass
                
            msg = bot.send_message(user_id, 
                           "❌ Неизвестная команда\n"
                           "Используйте меню для навигации")
            delete_message_with_delay(user_id, msg.message_id, 5)
            log_user_action(message.from_user, f"ввел неизвестную команду: {message.text}")
        else:
            # ЕСЛИ ЭТО ПРОСТО ТЕКСТ И РЕЖИМ ПОДДЕРЖКИ НЕ ВКЛЮЧЕН
            if user_id not in user_support_mode:
                msg = bot.send_message(user_id, 
                               "❌ Для обращения в поддержку сначала необходимо включить режим поддержки через меню")
                delete_message_with_delay(user_id, msg.message_id, 5)
                delete_message_with_delay(user_id, message.message_id, 5)
                
    except Exception as e:
        logger.error(f"Error in unknown command handler: {e}")

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
            logger.exception("Polling error: %s",e)
