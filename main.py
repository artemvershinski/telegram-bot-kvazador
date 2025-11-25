import logging
import os
import random
import string
import asyncio
from datetime import datetime, time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from flask import Flask
import threading

# Инициализация Flask для Render.com
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/health')
def health():
    return "OK"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")

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
        self.last_move_player_id = None
        self.last_activity = datetime.now()
        self.selected_cards = []  # Для хранения выбранных карт перед ходом
        
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
            self.last_activity = datetime.now()
            return True
        return False
    
    def remove_player(self, player_id: int):
        if player_id in self.players:
            index = self.players.index(player_id)
            self.players.remove(player_id)
            self.player_usernames.pop(index)
            self.last_activity = datetime.now()
            
            # Если игрок был текущим, переходим к следующему
            if index == self.current_player_index:
                self.current_player_index = self.current_player_index % len(self.players)
            elif index < self.current_player_index:
                self.current_player_index -= 1
                
            return True
        return False
    
    def start_game(self):
        if len(self.players) < 2:
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
        total_cards_needed = len(self.players) * cards_per_player
        
        # Если в колоде недостаточно карт, создаем дополнительную колоду
        while len(self.deck) < total_cards_needed:
            additional_deck = []
            additional_deck.extend(['queen'] * 6)
            additional_deck.extend(['king'] * 6)
            additional_deck.extend(['ace'] * 6)
            additional_deck.extend(['joker'] * 2)
            random.shuffle(additional_deck)
            self.deck.extend(additional_deck)
        
        for i, player_id in enumerate(self.players):
            start_index = i * cards_per_player
            end_index = start_index + cards_per_player
            self.player_hands[player_id] = self.deck[start_index:end_index]
        
        self.last_activity = datetime.now()
        return True, "Игра началась"
    
    def play_cards(self, player_id: int, card_count: int, selected_cards: list):
        if self.players[self.current_player_index] != player_id:
            return False, "Не ваш ход"
        
        if card_count < 1 or card_count > 3:
            return False, "Можно положить от 1 до 3 карт"
        
        hand = self.player_hands[player_id]
        if card_count > len(hand):
            return False, f"У тебя только {len(hand)} карт"
        
        # Проверяем, что выбранные карты есть в руке
        for card in selected_cards:
            if card not in hand:
                return False, f"У тебя нет карты {card}"
        
        # Берем реальные карты из руки (выбранные игроком)
        actual_cards = selected_cards[:card_count]
        
        # Удаляем выбранные карты из руки
        for card in actual_cards:
            if card in hand:
                hand.remove(card)
        
        # ВСЕГДА заявляем карты текущей темы, независимо от того, что на самом деле
        claimed_cards = [self.theme] * card_count
        
        self.table_cards.append({
            'player_id': player_id,
            'card_count': card_count,
            'claimed_cards': claimed_cards,  # Всегда заявляем карты темы
            'actual_cards': actual_cards,    # То, что на самом деле положили
            'timestamp': asyncio.get_event_loop().time()
        })
        
        self.last_move_player_id = player_id
        self.last_activity = datetime.now()
        
        # Проверяем победу
        if len(hand) == 0:
            return True, "ПОБЕДА! Ты сбросил все карты"
        
        self.current_player_index = (self.current_player_index + 1) % len(self.players)
        return True, f"Положил {card_count} карт"
    
    def can_challenge(self, challenger_id: int):
        """Может ли игрок проверять текущий ход"""
        if not self.table_cards:
            return False, "Нечего проверять"
        
        last_move = self.table_cards[-1]
        last_player_id = last_move['player_id']
        
        # Если осталось только 2 игрока, проверять может любой кроме того, кто сделал ход
        if len(self.players) == 2:
            return challenger_id != last_player_id, last_player_id
        
        # В обычном случае проверять может только следующий игрок
        last_player_index = self.players.index(last_player_id)
        next_player_index = (last_player_index + 1) % len(self.players)
        next_player_id = self.players[next_player_index]
        
        return challenger_id == next_player_id, next_player_id
    
    def challenge_player(self, challenger_id: int):
        can_challenge, expected_player_id = self.can_challenge(challenger_id)
        if not can_challenge:
            return False, "Вы не можете проверять этот ход"
        
        last_move = self.table_cards[-1]
        last_player_id = last_move['player_id']
        claimed_cards = last_move['claimed_cards']
        actual_cards = last_move['actual_cards']
        
        # Проверяем, совпадают ли заявленные карты с реальными по теме
        theme_cards_claimed = sum(1 for card in claimed_cards if card in [self.theme, 'joker'])
        theme_cards_actual = sum(1 for card in actual_cards if card in [self.theme, 'joker'])
        
        is_lying = theme_cards_claimed != theme_cards_actual
        
        if is_lying:
            # Игрок врал - проверяющий стреляет в него
            shooter_id = challenger_id
            target_id = last_player_id
        else:
            # Игрок не врал - проверяющий стреляет в себя
            shooter_id = challenger_id
            target_id = challenger_id
        
        result = self.fire_revolver(target_id)
        
        # Перераздача карт и новая тема только если игра продолжается
        if len(self.players) > 1:
            self.theme = random.choice(['queen', 'king', 'ace'])
            self.create_deck()
            
            # Новая раздача карт всем игрокам
            cards_per_player = 5
            total_cards_needed = len(self.players) * cards_per_player
            
            # Если в колоде недостаточно карт, создаем дополнительную колоду
            while len(self.deck) < total_cards_needed:
                additional_deck = []
                additional_deck.extend(['queen'] * 6)
                additional_deck.extend(['king'] * 6)
                additional_deck.extend(['ace'] * 6)
                additional_deck.extend(['joker'] * 2)
                random.shuffle(additional_deck)
                self.deck.extend(additional_deck)
            
            for i, player_id in enumerate(self.players):
                start_index = i * cards_per_player
                end_index = start_index + cards_per_player
                self.player_hands[player_id] = self.deck[start_index:end_index]
            
            self.table_cards = []
        
        self.last_activity = datetime.now()
        
        return True, {
            'challenger_id': challenger_id,
            'target_id': last_player_id,
            'is_lying': is_lying,
            'shooter_id': shooter_id,
            'survived': result,
            'claimed_cards': claimed_cards,
            'actual_cards': actual_cards
        }
    
    def fire_revolver(self, player_id: int):
        revolver = self.player_revolvers[player_id]
        
        if revolver['current_position'] == revolver['chamber']:
            # Игрок выбывает
            index = self.players.index(player_id)
            self.players.remove(player_id)
            self.player_usernames.pop(index)
            
            # Корректируем текущего игрока если нужно
            if index < self.current_player_index:
                self.current_player_index -= 1
            elif index == self.current_player_index and self.players:
                self.current_player_index = self.current_player_index % len(self.players)
            
            self.last_activity = datetime.now()
            return False
        else:
            revolver['current_position'] = (revolver['current_position'] + 1) % 6
            self.last_activity = datetime.now()
            return True
    
    def get_current_player(self):
        if not self.players:
            return None
        return self.players[self.current_player_index]
    
    def get_player_username(self, player_id: int):
        for i, pid in enumerate(self.players):
            if pid == player_id:
                return self.player_usernames[i]
        return "Игрок"

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

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    game = await find_user_game(user_id)
    if not game:
        await update.message.reply_text("Вы не в активной игре")
        return
    
    room_id = game.game_id
    
    # Удаляем игрока из игры
    game.remove_player(user_id)
    
    if len(game.players) < 2:
        # Если остался 1 игрок - завершаем игру
        if game.players:
            winner = game.get_player_username(game.players[0])
            await notify_players(game, context, f"🎉 ПОБЕДИТЕЛЬ: {winner}!")
        if room_id in active_games:
            del active_games[room_id]
        await update.message.reply_text("Вы вышли из игры. Комната удалена.")
    else:
        # Перезапускаем игру с оставшимися игроками
        game.game_state = "waiting"
        game.theme = None
        game.table_cards = []
        game.player_hands = {}
        game.player_revolvers = {}
        
        # Уведомляем остальных
        await notify_players(game, context, f"🚪 {username} вышел из игры. Перезапуск игры...")
        
        # Запускаем игру заново
        success, message = game.start_game()
        if success:
            theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы'}
            
            for player_id in game.players:
                try:
                    hand = game.player_hands.get(player_id, [])
                    hand_text = ", ".join([theme_names.get(card, card) for card in hand])
                    
                    await context.bot.send_message(
                        player_id,
                        f"🔄 Игра перезапущена!\n🎯 Тема: {theme_names.get(game.theme)}\n🎴 Твои карты: {hand_text}\n🔫 Револьвер заряжен!"
                    )
                except:
                    pass
            
            await show_game_state(game, context)
        
        await update.message.reply_text("Вы вышли из игры. Игра перезапущена для оставшихся игроков.")

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

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    logger.info(f"Callback received: {data} from user {user_id}")
    
    try:
        if data == "create_room":
            await create_room(update, context)
        elif data == "show_rules":
            await show_rules(update, context)
        elif data == "join_game":
            await join_game_info(update, context)
        elif data == "back_to_main":
            await back_to_main(update, context)
        elif data.startswith("join_room_"):
            room_id = data.split("_")[2]
            await join_room(update, context, room_id)
        elif data.startswith("start_room_"):
            room_id = data.split("_")[2]
            await start_room(update, context, room_id)
        elif data == "make_move":
            await show_move_interface(update, context)
        elif data.startswith("select_card_"):
            card_data = data.split("_")[2]
            await select_card_handler(update, context, card_data)
        elif data == "confirm_move":
            await confirm_move_handler(update, context)
        elif data == "clear_selection":
            await clear_selection_handler(update, context)
        elif data == "challenge":
            await challenge_handler(update, context)
        elif data.startswith("leave_room_"):
            room_id = data.split("_")[2]
            await leave_room(update, context, room_id)
        elif data == "back_to_game":
            game = await find_user_game(user_id)
            if game:
                await show_game_state(game, context)
            
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
    
    if len(game.players) < 2:
        await query.answer("Нужно минимум 2 игрока")
        return
    
    success, message = game.start_game()
    if success:
        theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы'}
        
        for player_id in game.players:
            try:
                hand = game.player_hands.get(player_id, [])
                hand_text = ", ".join([theme_names.get(card, card) for card in hand])
                
                await context.bot.send_message(
                    player_id,
                    f"🎮 Игра началась!\n🎯 Тема: {theme_names.get(game.theme)}\n🎴 Твои карты: {hand_text}\n🔫 Револьвер заряжен!"
                )
            except:
                pass
        
        await show_game_state(game, context)
    else:
        await query.answer(message)

async def show_move_interface(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    game = await find_user_game(user_id)
    if not game:
        await query.answer("Вы не в игре")
        return
    
    if game.players[game.current_player_index] != user_id:
        await query.answer("Не ваш ход")
        return
    
    # Очищаем предыдущий выбор
    game.selected_cards = []
    
    hand = game.player_hands.get(user_id, [])
    theme_names = {'queen': 'Q', 'king': 'K', 'ace': 'A', 'joker': 'J'}
    
    # Создаем кнопки для выбора карт
    keyboard = []
    row = []
    for i, card in enumerate(hand):
        card_symbol = theme_names.get(card, card)
        row.append(InlineKeyboardButton(card_symbol, callback_data=f"select_card_{i}"))
        if len(row) == 3:  # 3 кнопки в ряд
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # Кнопки управления
    keyboard.extend([
        [InlineKeyboardButton("✅ Заявить", callback_data="confirm_move")],
        [InlineKeyboardButton("🗑️ Очистить выбор", callback_data="clear_selection")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_game")]
    ])
    
    await query.edit_message_text(
        "🎴 Выбери карты для хода (макс. 3):\n\n"
        "Выбранные карты: Нет",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def select_card_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, card_index: str):
    query = update.callback_query
    user_id = query.from_user.id
    
    game = await find_user_game(user_id)
    if not game:
        await query.answer("Вы не в игре")
        return
    
    index = int(card_index)
    hand = game.player_hands.get(user_id, [])
    
    if index >= len(hand):
        await query.answer("Неверная карта")
        return
    
    selected_card = hand[index]
    
    # Проверяем, не превышен ли лимит
    if len(game.selected_cards) >= 3:
        await query.answer("Можно выбрать максимум 3 карты")
        return
    
    # Добавляем карту в выбранные
    game.selected_cards.append(selected_card)
    
    # Обновляем интерфейс
    theme_names = {'queen': 'Q', 'king': 'K', 'ace': 'A', 'joker': 'J'}
    selected_text = ", ".join([theme_names.get(card, card) for card in game.selected_cards])
    
    hand = game.player_hands.get(user_id, [])
    keyboard = []
    row = []
    for i, card in enumerate(hand):
        card_symbol = theme_names.get(card, card)
        # Помечаем выбранные карты
        if card in game.selected_cards:
            card_symbol = f"✅{card_symbol}"
        row.append(InlineKeyboardButton(card_symbol, callback_data=f"select_card_{i}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.extend([
        [InlineKeyboardButton("✅ Заявить", callback_data="confirm_move")],
        [InlineKeyboardButton("🗑️ Очистить выбор", callback_data="clear_selection")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_game")]
    ])
    
    await query.edit_message_text(
        f"🎴 Выбери карты для хода (макс. 3):\n\n"
        f"Выбранные карты: {selected_text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    await query.answer(f"Выбрана карта: {theme_names.get(selected_card, selected_card)}")

async def clear_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    game = await find_user_game(user_id)
    if not game:
        await query.answer("Вы не в игре")
        return
    
    game.selected_cards = []
    
    await show_move_interface(update, context)
    await query.answer("Выбор очищен")

async def confirm_move_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    game = await find_user_game(user_id)
    if not game:
        await query.answer("Вы не в игре")
        return
    
    if not game.selected_cards:
        await query.answer("Сначала выбери карты")
        return
    
    card_count = len(game.selected_cards)
    selected_cards = game.selected_cards.copy()
    
    success, message = game.play_cards(user_id, card_count, selected_cards)
    
    if success:
        if "ПОБЕДА" in message:
            await notify_players(game, context, f"🎉 {game.get_player_username(user_id)} ПОБЕДИЛ!")
            # Автоматически удаляем комнату после победы
            if game.game_id in active_games:
                del active_games[game.game_id]
            return
        
        # Уведомляем всех о ходе
        theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы', 'joker': 'Джокеры'}
        claimed_text = ", ".join([theme_names.get(card, card) for card in [game.theme] * card_count])
        
        move_message = (
            f"🎴 {game.get_player_username(user_id)} походил!\n"
            f"📦 Положил карт: {card_count}\n"
            f"💬 Заявил: {claimed_text}\n\n"
            f"🎯 Следующий ход: {game.get_player_username(game.get_current_player())}"
        )
        
        await notify_players(game, context, move_message)
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
    
    can_challenge, expected_player_id = game.can_challenge(user_id)
    if not can_challenge:
        await query.answer("Сейчас не ваша очередь проверять")
        return
    
    last_move = game.table_cards[-1]
    target_player_id = last_move['player_id']
    
    # Анимация проверки для всех игроков
    challenge_message = (
        f"🔍 {game.get_player_username(user_id)} считает, что {game.get_player_username(target_player_id)} врет...\n"
        f"⏳ Сейчас посмотрим..."
    )
    
    await notify_players(game, context, challenge_message)
    await asyncio.sleep(2)
    
    success, result = game.challenge_player(user_id)
    
    if success:
        # Показываем результат проверки
        theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы', 'joker': 'Джокеры'}
        claimed_text = ", ".join([theme_names.get(card, card) for card in result['claimed_cards']])
        actual_text = ", ".join([theme_names.get(card, card) for card in result['actual_cards']])
        
        result_message = (
            f"📋 Заявлено: {claimed_text}\n"
            f"🎴 Реально: {actual_text}\n"
            f"❌ Врун: {'ДА' if result['is_lying'] else 'НЕТ'}"
        )
        
        await notify_players(game, context, result_message)
        await asyncio.sleep(1.5)
        
        # Анимация выстрела
        target_username = game.get_player_username(result['target_id'])
        shoot_messages = [
            f"🔫 {target_username} берет револьвер...",
            f"💀 Подносит к виску...",
            f"🎯 Нажимает на курок..."
        ]
        
        for msg in shoot_messages:
            await notify_players(game, context, msg)
            await asyncio.sleep(1.5)
        
        if result['survived']:
            await notify_players(game, context, "✅ ОСЕЧКА!")
            await asyncio.sleep(1)
            
            # Если остался только 1 игрок - он побеждает
            if len(game.players) == 1:
                winner = game.get_player_username(game.players[0])
                await notify_players(game, context, f"🎉 ПОБЕДИТЕЛЬ: {winner}!")
                if game.game_id in active_games:
                    del active_games[game.game_id]
                return
        else:
            await notify_players(game, context, f"💥 ВЫСТРЕЛ! {target_username} выбывает!")
            await asyncio.sleep(3)
            
            # Если остался только 1 игрок - он побеждает
            if len(game.players) == 1:
                winner = game.get_player_username(game.players[0])
                await notify_players(game, context, f"🎉 ПОБЕДИТЕЛЬ: {winner}!")
                if game.game_id in active_games:
                    del active_games[game.game_id]
                return
        
        # Показываем новое состояние игры
        if len(game.players) > 1:
            await show_game_state(game, context)
        else:
            winner = game.get_player_username(game.players[0])
            await notify_players(game, context, f"🎉 ПОБЕДИТЕЛЬ: {winner}!")
            # Автоматически удаляем комнату после победы
            if game.game_id in active_games:
                del active_games[game.game_id]

async def show_game_state(game, context):
    current_player = game.get_current_player()
    if not current_player:
        return
        
    theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы'}
    
    for player_id in game.players:
        try:
            hand = game.player_hands.get(player_id, [])
            hand_text = ", ".join([theme_names.get(card, card) for card in hand])
            
            message = (
                f"🎯 Тема раунда: {theme_names.get(game.theme)}\n"
                f"🎴 Твои карты: {hand_text}\n"
                f"👥 Игроков осталось: {len(game.players)}\n\n"
            )
            
            if player_id == current_player:
                message += "✅ Сейчас ТВОЙ ход!"
                keyboard = [
                    [InlineKeyboardButton("🎴 Походить", callback_data="make_move")],
                ]
            else:
                # Проверяем, может ли игрок проверять
                can_challenge, _ = game.can_challenge(player_id)
                if can_challenge and game.table_cards:
                    last_player = game.table_cards[-1]['player_id']
                    message += f"🔍 Можешь проверить {game.get_player_username(last_player)}!"
                    keyboard = [
                        [InlineKeyboardButton("🔍 Проверить игрока", callback_data="challenge")],
                    ]
                else:
                    message += f"⏳ Сейчас ходит {game.get_player_username(current_player)}"
                    keyboard = []
            
            await context.bot.send_message(player_id, message, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения игроку {player_id}: {e}")

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
    
    # Правильно получаем username
    username = None
    for i, pid in enumerate(game.players):
        if pid == user_id:
            username = game.player_usernames[i]
            break
    
    if not username:
        username = "Игрок"
    
    game.remove_player(user_id)
    
    if len(game.players) == 0:
        # Автоматически удаляем комнату, когда все вышли
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

async def show_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rules_text = (
        "Правила Liar's Bar:\n\n"
        "• 2-4 игрока\n• Каждому по 5 карт\n• Тема: Дамы, Короли или Тузы\n"
        "• Ход: положи 1-3 карты рубашкой вверх\n• Всегда заявляй карты текущей темы!\n"
        "• Следующий игрок может проверить предыдущего\n"
        "• Если проверка неудачная - русская рулетка\n"
        "• В револьвере 6 патронов, 1 боевой\n• Выбываешь при выстреле\n"
        "• Последний выживший побеждает\n\n"
        "Команды:\n"
        "/start - главное меню\n"
        "/join [ID] - присоединиться\n"
        "/stop - выйти из текущей игры"
    )
    await query.edit_message_text(rules_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="back_to_main")]]))

async def join_game_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Используй команду: /join [ID_комнаты]\n\nНапример: /join 123456")

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("Создать комнату", callback_data="create_room")],
        [InlineKeyboardButton("Правила игры", callback_data="show_rules")],
        [InlineKeyboardButton("Присоединиться к игре", callback_data="join_game")]
    ]
    await query.edit_message_text("Главное меню:", reply_markup=InlineKeyboardMarkup(keyboard))

def cleanup_inactive_games():
    """Очистка неактивных игр (старше 2 часов)"""
    current_time = datetime.now()
    rooms_to_delete = []
    
    for room_id, game in active_games.items():
        time_diff = current_time - game.last_activity
        if time_diff.total_seconds() > 7200:  # 2 часа
            rooms_to_delete.append(room_id)
    
    for room_id in rooms_to_delete:
        del active_games[room_id]
        logger.info(f"Удалена неактивная комната {room_id}")

async def send_cleanup_warning(context: ContextTypes.DEFAULT_TYPE):
    """Отправка предупреждения о скорой очистке"""
    current_time = datetime.now().time()
    warning_time = time(20, 45)  # 20:45 UTC
    
    if current_time.hour == warning_time.hour and current_time.minute == warning_time.minute:
        if active_games:
            warning_message = "⚠️ ВНИМАНИЕ: В 21:00 UTC все активные игры будут автоматически завершены для технического обслуживания!"
            for game in active_games.values():
                for player_id in game.players:
                    try:
                        await context.bot.send_message(player_id, warning_message)
                    except:
                        pass
            logger.info("Отправлены предупреждения о скорой очистке")

async def perform_daily_cleanup(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневная очистка в 21:00 UTC"""
    current_time = datetime.now().time()
    cleanup_time = time(21, 0)  # 21:00 UTC
    
    if current_time.hour == cleanup_time.hour and current_time.minute == cleanup_time.minute:
        if active_games:
            cleanup_message = "🔄 Техническое обслуживание: все активные игры завершены. Создавайте новые комнаты!"
            for game in list(active_games.values()):
                for player_id in game.players:
                    try:
                        await context.bot.send_message(player_id, cleanup_message)
                    except:
                        pass
            active_games.clear()
            logger.info("Выполнена ежедневная очистка всех комнат")

def schedule_cleanup_tasks(application):
    """Планирование задач очистки"""
    async def cleanup_callback(context: ContextTypes.DEFAULT_TYPE):
        cleanup_inactive_games()
        await send_cleanup_warning(context)
        await perform_daily_cleanup(context)
    
    # Запускаем проверку каждую минуту
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_callback, interval=60, first=10)  # Каждую минуту

def run_flask():
    """Запуск Flask сервера для Render.com"""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Запуск Flask сервера на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_bot():
    """Запуск Telegram бота"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Планируем задачи очистки
    schedule_cleanup_tasks(application)
    
    logger.info("Telegram бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    """Основная функция запуска"""
    logger.info("Запуск приложения...")
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Запускаем бота в основном потоке
    run_bot()

if __name__ == "__main__":
    main()
