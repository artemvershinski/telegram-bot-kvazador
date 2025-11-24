import logging
import os
import asyncio
import random
import string
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from flask import Flask

# Инициализация Flask для Render.com
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")

# Храним игры в памяти (без базы)
active_games = {}

class LiarsBarGame:
    def __init__(self, game_id: str, creator_id: int):
        self.game_id = game_id
        self.players = [creator_id]
        self.player_usernames = []
        self.game_state = "waiting"
        self.theme = None
        self.table_cards = []
        self.current_player_index = 0
        self.player_hands = {}
        self.player_revolvers = {}
        self.deck = []
        
    def create_deck(self):
        self.deck = []
        self.deck.extend(['queen'] * 6)
        self.deck.extend(['king'] * 6)
        self.deck.extend(['ace'] * 6)
        self.deck.extend(['joker'] * 2)
        random.shuffle(self.deck)
    
    def add_player(self, player_id: int, username: str):
        if player_id not in self.players:
            self.players.append(player_id)
            self.player_usernames.append(username)
            return True
        return False
    
    def remove_player(self, player_id: int):
        if player_id in self.players:
            index = self.players.index(player_id)
            self.players.remove(player_id)
            self.player_usernames.pop(index)
            return True
        return False
    
    def start_game(self):
        if len(self.players) < 4:
            return False, "Недостаточно игроков"
        
        self.game_state = "playing"
        self.create_deck()
        
        for player_id in self.players:
            self.player_revolvers[player_id] = {
                'chamber': random.randint(0, 5),
                'current_position': 0
            }
        
        self.theme = random.choice(['queen', 'king', 'ace'])
        
        # Раздача карт
        cards_per_player = 5
        for i, player_id in enumerate(self.players):
            start_index = i * cards_per_player
            end_index = start_index + cards_per_player
            self.player_hands[player_id] = self.deck[start_index:end_index]
        
        return True, "Игра началась"
    
    def play_cards(self, player_id: int, card_count: int):
        if self.players[self.current_player_index] != player_id:
            return False, "Не ваш ход"
        
        if card_count < 1 or card_count > 3:
            return False, "Можно положить от 1 до 3 карт"
        
        hand = self.player_hands[player_id]
        if card_count > len(hand):
            return False, f"У тебя только {len(hand)} карт"
        
        actual_cards = random.sample(hand, card_count)
        for card in actual_cards:
            hand.remove(card)
        
        self.table_cards.append({
            'player_id': player_id,
            'card_count': card_count,
            'actual_cards': actual_cards
        })
        
        if len(hand) == 0:
            return True, "ПОБЕДА! Ты сбросил все карты"
        
        self.current_player_index = (self.current_player_index + 1) % len(self.players)
        return True, f"Положил {card_count} карт"
    
    def challenge_previous_player(self, player_id: int):
        if not self.table_cards:
            return False, "Нечего проверять"
        
        previous_move = self.table_cards[-1]
        previous_player_id = previous_move['player_id']
        
        has_theme_cards = any(card in [self.theme, 'joker'] for card in previous_move['actual_cards'])
        
        if has_theme_cards:
            shooter_id = player_id
        else:
            shooter_id = previous_player_id
        
        result = self.fire_revolver(shooter_id)
        
        if len(self.players) > 1:
            self.theme = random.choice(['queen', 'king', 'ace'])
            self.start_game()  # Перезапускаем игру
            self.table_cards = []
    
        return True, {
            'shooter': shooter_id,
            'survived': result
        }
    
    def fire_revolver(self, player_id: int):
        revolver = self.player_revolvers[player_id]
        
        if revolver['current_position'] == revolver['chamber']:
            index = self.players.index(player_id)
            self.players.remove(player_id)
            self.player_usernames.pop(index)
            return False
        else:
            revolver['current_position'] = (revolver['current_position'] + 1) % 6
            return True
    
    def get_current_player(self):
        return self.players[self.current_player_index]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Создать комнату", callback_data="create_room")],
        [InlineKeyboardButton("Правила игры", callback_data="show_rules")],
        [InlineKeyboardButton("Присоединиться к игре", callback_data="join_game")]
    ]
    
    await update.message.reply_text(
        f"Привет {update.effective_user.first_name}!\nWerb Hub - Liar's Bar\n\nВыбери действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    
    try:
        if data == "create_room":
            await create_room(update, context)
            
        elif data == "show_rules":
            rules_text = (
                "Правила Liar's Bar:\n\n"
                "• 4 игрока\n• Каждому по 5 карт\n• Тема: Дамы, Короли или Тузы\n"
                "• Ход: положи 1-3 карты рубашкой вверх\n• Можно обманывать!\n"
                "• Следующий игрок может проверить предыдущего\n"
                "• Если проверка неудачная - русская рулетка\n"
                "• В револьвере 6 патронов, 1 боевой\n• Выбываешь при выстреле\n"
                "• Последний выживший побеждает"
            )
            await query.edit_message_text(rules_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back_to_main")]]))
            
        elif data == "join_game":
            await query.edit_message_text("Используй команду: /join [ID_комнаты]\n\nНапример: /join 123456")
            
        elif data == "back_to_main":
            keyboard = [
                [InlineKeyboardButton("Создать комнату", callback_data="create_room")],
                [InlineKeyboardButton("Правила игры", callback_data="show_rules")],
                [InlineKeyboardButton("Присоединиться к игре", callback_data="join_game")]
            ]
            await query.edit_message_text("Главное меню:", reply_markup=InlineKeyboardMarkup(keyboard))
            
        elif data.startswith("join_room_"):
            room_id = data.split("_")[2]
            await join_room(update, context, room_id)
            
        elif data.startswith("start_room_"):
            room_id = data.split("_")[2]
            await start_room(update, context, room_id)
            
        elif data.startswith("play_cards_"):
            card_count = int(data.split("_")[2])
            await play_cards_handler(update, context, card_count)
            
        elif data == "challenge":
            await challenge_handler(update, context)
            
        elif data.startswith("leave_room_"):
            room_id = data.split("_")[2]
            await leave_room(update, context, room_id)
            
    except Exception as e:
        logger.error(f"Ошибка в callback: {e}")
        await query.answer("Ошибка")

async def create_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    
    room_id = ''.join(random.choices(string.digits, k=6))
    game = LiarsBarGame(room_id, user_id)
    game.player_usernames.append(f"@{username}")
    active_games[room_id] = game
    
    players_text = "\n".join([f"• {name}" for name in game.player_usernames])
    
    keyboard = [
        [InlineKeyboardButton("Присоединиться", callback_data=f"join_room_{room_id}")],
        [InlineKeyboardButton("Начать игру", callback_data=f"start_room_{room_id}")],
        [InlineKeyboardButton("Выйти", callback_data=f"leave_room_{room_id}")]
    ]
    
    await query.edit_message_text(
        f"Комната создана!\n\nID: {room_id}\nИгроков: 1/4\n\nИгроки:\n{players_text}\n\nОтправь ID друзьям:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def join_room(update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str):
    query = update.callback_query
    user_id = query.from_user.id
    username = query.from_user.username or query.from_user.first_name
    
    if room_id not in active_games:
        await query.answer("Комната не найдена")
        return
    
    game = active_games[room_id]
    
    if user_id in game.players:
        await query.answer("Вы уже в комнате")
        return
        
    if len(game.players) >= 4:
        await query.answer("Комната заполнена")
        return
    
    game.add_player(user_id, f"@{username}")
    
    # Уведомляем всех
    for player_id in game.players:
        if player_id != user_id:
            try:
                await context.bot.send_message(player_id, f"@{username} присоединился к комнате")
            except:
                pass
    
    players_text = "\n".join([f"• {name}" for name in game.player_usernames])
    
    keyboard = []
    if game.players[0] == user_id:
        keyboard.append([InlineKeyboardButton("Начать игру", callback_data=f"start_room_{room_id}")])
    
    keyboard.extend([
        [InlineKeyboardButton("Присоединиться", callback_data=f"join_room_{room_id}")],
        [InlineKeyboardButton("Выйти", callback_data=f"leave_room_{room_id}")]
    ])
    
    await query.edit_message_text(
        f"Комната {room_id}\nИгроков: {len(game.players)}/4\n\nИгроки:\n{players_text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    await query.answer("Вы присоединились!")

async def start_room(update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str):
    query = update.callback_query
    user_id = query.from_user.id
    
    if room_id not in active_games:
        await query.answer("Комната не найдена")
        return
    
    game = active_games[room_id]
    
    if game.players[0] != user_id:
        await query.answer("Только создатель может начать игру")
        return
    
    if len(game.players) < 4:
        await query.answer("Нужно 4 игрока")
        return
    
    success, message = game.start_game()
    if success:
        theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы'}
        
        for player_id in game.players:
            try:
                hand = game.player_hands.get(player_id, [])
                hand_text = ", ".join(hand)
                
                await context.bot.send_message(
                    player_id,
                    f"Игра началась!\nТема: {theme_names.get(game.theme)}\nТвои карты: {hand_text}\nРевольвер заряжен!"
                )
            except:
                pass
        
        await show_game_state(game, context)
    else:
        await query.answer(message)

async def play_cards_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, card_count: int):
    query = update.callback_query
    user_id = query.from_user.id
    
    game = await find_user_game(user_id)
    if not game:
        await query.answer("Вы не в игре")
        return
    
    success, message = game.play_cards(user_id, card_count)
    if success:
        if "ПОБЕДА" in message:
            await notify_players(game, context, "Игрок победил!")
            del active_games[game.game_id]
        else:
            await notify_players(game, context, f"Игрок положил {card_count} карт")
            await show_game_state(game, context)
    else:
        await query.answer(message)

async def challenge_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    game = await find_user_game(user_id)
    if not game:
        await query.answer("Вы не в игре")
        return
    
    success, result = game.challenge_previous_player(user_id)
    if success:
        shooter_id = result['shooter']
        survived = result['survived']
        
        shooter_name = "Вы" if shooter_id == user_id else "Игрок"
        
        if survived:
            message = f"{shooter_name} выстрелил и выжил!"
        else:
            message = f"{shooter_name} выстрелил и выбыл!"
        
        await notify_players(game, context, message)
        
        if len(game.players) > 1:
            await show_game_state(game, context)
        else:
            await notify_players(game, context, f"Игрок победил!")
            del active_games[game.game_id]
    else:
        await query.answer(result)

async def leave_room(update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str):
    query = update.callback_query
    user_id = query.from_user.id
    
    if room_id not in active_games:
        await query.answer("Комната не найдена")
        return
    
    game = active_games[room_id]
    
    if user_id not in game.players:
        await query.answer("Вы не в комнате")
        return
    
    username = next((name for i, pid in enumerate(game.players) if pid == user_id), "Игрок")
    game.remove_player(user_id)
    
    if len(game.players) == 0:
        del active_games[room_id]
        await query.edit_message_text("Вы вышли. Комната удалена.")
    else:
        # Уведомляем остальных
        await notify_players(game, context, f"{username} вышел из комнаты")
        
        players_text = "\n".join([f"• {name}" for name in game.player_usernames])
        keyboard = [
            [InlineKeyboardButton("Присоединиться", callback_data=f"join_room_{room_id}")],
            [InlineKeyboardButton("Выйти", callback_data=f"leave_room_{room_id}")]
        ]
        
        await query.edit_message_text(
            f"Комната {room_id}\nИгроков: {len(game.players)}/4\n\nИгроки:\n{players_text}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def find_user_game(user_id: int):
    for game in active_games.values():
        if user_id in game.players:
            return game
    return None

async def notify_players(game, context, message):
    for player_id in game.players:
        try:
            await context.bot.send_message(player_id, message)
        except:
            pass

async def show_game_state(game, context):
    current_player = game.get_current_player()
    theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы'}
    
    for player_id in game.players:
        try:
            hand = game.player_hands.get(player_id, [])
            hand_text = ", ".join(hand)
            
            message = (
                f"Тема: {theme_names.get(game.theme)}\n"
                f"Твои карты: {hand_text}\n"
                f"Карт на столе: {len(game.table_cards)}\n"
                f"Игроков: {len(game.players)}\n\n"
            )
            
            if player_id == current_player:
                message += "Сейчас твой ход!"
                keyboard = [
                    [InlineKeyboardButton("Положить 1 карту", callback_data="play_cards_1")],
                    [InlineKeyboardButton("Положить 2 карты", callback_data="play_cards_2")],
                    [InlineKeyboardButton("Положить 3 карты", callback_data="play_cards_3")],
                    [InlineKeyboardButton("Проверить", callback_data="challenge")]
                ]
            else:
                current_name = next((name for i, pid in enumerate(game.players) if pid == current_player), "Игрок")
                message += f"Сейчас ходит {current_name}"
                keyboard = [[InlineKeyboardButton("Проверить", callback_data="challenge")]]
            
            await context.bot.send_message(player_id, message, reply_markup=InlineKeyboardMarkup(keyboard))
        except:
            pass

async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи ID комнаты: /join 123456")
        return
    
    room_id = context.args[0]
    if room_id in active_games:
        keyboard = [[InlineKeyboardButton("Присоединиться", callback_data=f"join_room_{room_id}")]]
        await update.message.reply_text(f"Комната {room_id} найдена:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("Комната не найдена")

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    logger.info("Бот запущен")
    
    # Для Render.com используем вебхуки, для локальной разработки - поллинг
    if os.getenv('RENDER'):
        # Вебхук для продакшена на Render.com
        webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{BOT_TOKEN}"
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get('PORT', 10000)),
            url_path=BOT_TOKEN,
            webhook_url=webhook_url
        )
    else:
        # Поллинг для локальной разработки
        application.run_polling()

if __name__ == "__main__":
    # Запускаем Flask для здоровья приложения на Render
    if os.getenv('RENDER'):
        import threading
        threading.Thread(target=main, daemon=True).start()
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
    else:
        main()
