import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
import sqlite3
import datetime
import os
from flask import Flask
from threading import Thread
import time

# Flask для keep-alive
app = Flask('')

@app.route('/')
def home():
    return "🤖 Bot is alive and running!"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# Telegram токен
BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

def init_db():
    conn = sqlite3.connect('users.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT)''')
    conn.commit()
    conn.close()

def register_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect('users.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?)", 
              (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

# 👇 Убедись, что это именно int, как ты просил
ADMIN_ID = 8401905691

user_reply_mode = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = int(message.from_user.id)
    register_user(user_id, 
                  message.from_user.username, 
                  message.from_user.first_name, 
                  message.from_user.last_name)

    welcome_text = """
Привет. Я бот-пересыльщик сообщений для kvzdr.
Для связи с kvzdr сначала вам необходимо отправить сообщение (сколько потребуется) здесь. 
Ответ может поступить через данного бота, либо вам в ЛС.

Ваше сообщение будет доставлено ему от вашего имени.

Сам kvzdr свяжется с вами как только заметит ваше сообщение в боте. Просто представьте что это чат с ним, а не какой-то чат с ботом-пересыльщиком сообщений.
    """

    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("📞 Попросить связаться со мной."))
    bot.send_message(user_id, welcome_text, reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "📞 Попросить связаться со мной.")
def handle_contact_request(message):
    user_id = int(message.from_user.id)
    bot.send_message(user_id, "✅ Ваш запрос на связь отправлен. Ожидайте ответа.")
    bot.send_message(
        ADMIN_ID,
        f"📞 Пользователь {message.from_user.first_name} "
        f"@{message.from_user.username or 'без username'} "
        f"(ID: {user_id}) просит связаться."
    )

@bot.message_handler(commands=['reply'])
def start_reply_mode(message):
    user_id = int(message.from_user.id)
    if user_id != ADMIN_ID:
        bot.send_message(user_id, "❌ Эта команда только для администратора.")
        return

    try:
        target_id = int(message.text.split()[1])
        user_reply_mode[ADMIN_ID] = target_id
        bot.send_message(ADMIN_ID, f"🔹 Режим ответа включен для пользователя ID: {target_id}")
    except (IndexError, ValueError):
        bot.send_message(ADMIN_ID, "❌ Используй: /reply user_id\nПример: /reply 123456789")

@bot.message_handler(commands=['stop'])
def stop_reply_mode(message):
    user_id = int(message.from_user.id)
    if user_id == ADMIN_ID:
        if ADMIN_ID in user_reply_mode:
            del user_reply_mode[ADMIN_ID]
            bot.send_message(ADMIN_ID, "🔹 Режим ответа выключен.")
        else:
            bot.send_message(ADMIN_ID, "🔹 Режим ответа не был включен.")

@bot.message_handler(func=lambda message: int(message.from_user.id) == ADMIN_ID and ADMIN_ID in user_reply_mode)
def handle_admin_reply(message):
    if message.content_type != 'text':
        bot.send_message(ADMIN_ID, "❌ В режиме ответа можно отправлять только текст.")
        return

    target_user_id = user_reply_mode[ADMIN_ID]

    try:
        bot.send_message(target_user_id, f"💌 Ответ от kvzdr:\n\n{message.text}")
        bot.send_message(ADMIN_ID, f"✅ Ответ отправлен пользователю ID: {target_user_id}")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Ошибка отправки: {e}")

@bot.message_handler(content_types=['text'])
def forward_text_message(message):
    user_id = int(message.from_user.id)

    if message.text.startswith('/'):
        return

    if message.text == "📞 Попросить связаться со мной.":
        return  # Уже обрабатывается

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

    try:
        bot.send_message(ADMIN_ID, f"{user_info}\n\n📨 Сообщение:\n\n{message.text}")
        bot.send_message(user_id, "✅ Сообщение отправлено администратору!")
    except:
        bot.send_message(user_id, "❌ Ошибка отправки. Администратор не найден.")

@bot.message_handler(content_types=['photo', 'voice', 'video', 'document', 'audio'])
def forward_media_message(message):
    user_id = int(message.from_user.id)

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

    try:
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

        bot.send_message(user_id, "✅ Медиа-сообщение отправлено kvzdr!")
    except Exception as e:
        print(f"Ошибка отправки медиа: {e}")
        bot.send_message(user_id, "❌ Ошибка отправки медиа.")

@bot.message_handler(content_types=['contact', 'location'])
def forward_contact_location(message):
    user_id = int(message.from_user.id)

    user_info = f"👤 От: {message.from_user.first_name}"
    if message.from_user.username:
        user_info += f" (@{message.from_user.username})"
    user_info += f"\n🆔 ID: {user_id}"
    user_info += f"\n⏰ {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}"

    try:
        if message.contact:
            bot.send_contact(
                ADMIN_ID,
                message.contact.phone_number,
                message.contact.first_name,
            )
            bot.send_message(ADMIN_ID, f"{user_info}\n📞 Прислал контакт")
        elif message.location:
            bot.send_location(
                ADMIN_ID,
                message.location.latitude,
                message.location.longitude,
            )
            bot.send_message(ADMIN_ID, f"{user_info}\n📍 Прислал локацию")

        bot.send_message(user_id, "✅ Данные отправлены kvzdr!")
    except Exception as e:
        print(f"Ошибка отправки контакта/локации: {e}")
        bot.send_message(user_id, "❌ Ошибка отправки.")

def start_bot():
    try:
        print("🔄 Инициализация базы данных...")
        init_db()

        print("🔍 Проверка токена...")
        if not BOT_TOKEN:
            print("❌ BOT_TOKEN не найден!")
            return

        print("🤖 Запуск Telegram бота...")
        print(f"✅ Токен: {BOT_TOKEN[:10]}...")

        bot.get_me()
        print("✅ Подключение к Telegram успешно!")
        print("🎯 Бот готов принимать сообщения...")

        # Бесконечный polling с защитой от сбоев
        while True:
            try:
                bot.infinity_polling(
                    timeout=60,
                    long_polling_timeout=60,
                    logger_level="INFO"
                )
            except Exception as e:
                print(f"❌ Ошибка polling: {e}")
                print("🔄 Перезапуск через 10 секунд...")
                time.sleep(10)

    except Exception as e:
        print(f"💥 Критическая ошибка при запуске: {e}")
        print("🔄 Перезапуск через 30 секунд...")
        time.sleep(30)
        start_bot()  # Рекурсивный перезапуск при фатальной ошибке

if __name__ == "__main__":
    keep_alive()  # Запуск Flask-сервера для keep-alive
    start_bot()   # Запуск самого Telegram-ботач
