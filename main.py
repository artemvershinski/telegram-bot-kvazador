import logging
import os
import random
import string
import asyncio
from datetime import datetime, time
import threading
import time as time_module
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")

active_games = {}
game_cleanup_scheduled = False

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
        
        self.last_activity = datetime.now()
        return True, "Игра началась"
    
    def play_cards(self, player_id: int, card_count: int, claimed_cards: list):
        if self.players[self.current_player_index] != player_id:
            return False, "Не ваш ход"
        
        if card_count < 1 or card_count > 3:
            return False, "Можно положить от 1 до 3 карт"
        
        hand = self.player_hands[player_id]
        if card_count > len(hand):
            return False, f"У тебя только {len(hand)} карт"
        
        # Берем реальные карты из руки
        actual_cards = random.sample(hand, card_count)
        for card in actual_cards:
            hand.remove(card)
        
        self.table_cards.append({
            'player_id': player_id,
            'card_count': card_count,
            'claimed_cards': claimed_cards,  # То, что игрок заявил
            'actual_cards': actual_cards,    # То, что на самом деле
            'timestamp': asyncio.get_event_loop().time()
        })
        
        self.last_move_player_id = player_id
        self.last_activity = datetime.now()
        
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
        
        # Проверять может только следующий игрок после того, кто сделал ход
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
            shooter_id = last_player_id
            target_id = last_player_id
        else:
            # Игрок не врал - проверяющий стреляет в себя
            shooter_id = challenger_id
            target_id = challenger_id
        
        result = self.fire_revolver(shooter_id)
        
        # Перераздача карт и новая тема
        self.theme = random.choice(['queen', 'king', 'ace'])
        self.create_deck()
        
        # Новая раздача карт всем игрокам
        cards_per_player = 5
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
            index = self.players.index(player_id)
            self.players.remove(player_id)
            self.player_usernames.pop(index)
            self.last_activity = datetime.now()
            return False
        else:
            revolver['current_position'] = (revolver['current_position'] + 1) % 6
            self.last_activity = datetime.now()
            return True
    
    def get_current_player(self):
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
        elif data.startswith("claim_cards_"):
            card_data = data.split("_")[2]
            await process_card_claim(update, context, card_data)
        elif data.startswith("final_move_"):
            parts = data.split("_")
            card_count = int(parts[2])
            card_type = parts[3]
            await finalize_move(update, context, card_count, card_type)
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
    
    if len(game.players) < 4:
        await query.answer("Нужно 4 игрока")
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
    
    keyboard = [
        [InlineKeyboardButton("1 карта", callback_data="claim_cards_1")],
        [InlineKeyboardButton("2 карты", callback_data="claim_cards_2")],
        [InlineKeyboardButton("3 карты", callback_data="claim_cards_3")],
        [InlineKeyboardButton("Отмена", callback_data="back_to_game")]
    ]
    
    await query.edit_message_text(
        "Сколько карт будешь класть?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def process_card_claim(update: Update, context: ContextTypes.DEFAULT_TYPE, card_count: str):
    query = update.callback_query
    user_id = query.from_user.id
    
    game = await find_user_game(user_id)
    if not game:
        await query.answer("Вы не в игре")
        return
    
    card_count_int = int(card_count)
    
    # Показываем интерфейс выбора карт
    theme_names = {'queen': 'Q', 'king': 'K', 'ace': 'A'}
    current_theme = theme_names.get(game.theme, game.theme)
    
    keyboard = [
        [InlineKeyboardButton(f"{current_theme}", callback_data=f"final_move_{card_count_int}_{game.theme}")],
        [InlineKeyboardButton("Q", callback_data=f"final_move_{card_count_int}_queen")],
        [InlineKeyboardButton("K", callback_data=f"final_move_{card_count_int}_king")],
        [InlineKeyboardButton("A", callback_data=f"final_move_{card_count_int}_ace")],
        [InlineKeyboardButton("J", callback_data=f"final_move_{card_count_int}_joker")],
        [InlineKeyboardButton("Смешанные", callback_data=f"final_move_{card_count_int}_mixed")],
    ]
    
    await query.edit_message_text(
        f"Выбери какие карты будешь заявлять ({card_count} шт.):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def finalize_move(update: Update, context: ContextTypes.DEFAULT_TYPE, card_count: int, card_type: str):
    query = update.callback_query
    user_id = query.from_user.id
    
    game = await find_user_game(user_id)
    if not game:
        await query.answer("Вы не в игре")
        return
    
    # Создаем список заявленных карт
    if card_type == "mixed":
        # Для смешанных - случайный набор
        themes = ['queen', 'king', 'ace', 'joker']
        claimed_cards = [random.choice(themes) for _ in range(card_count)]
    else:
        claimed_cards = [card_type] * card_count
    
    success, message = game.play_cards(user_id, card_count, claimed_cards)
    
    if success:
        if "ПОБЕДА" in message:
            await notify_players(game, context, f"🎉 {game.get_player_username(user_id)} ПОБЕДИЛ!")
            # Автоматически удаляем комнату после победы
            if game.game_id in active_games:
                del active_games[game.game_id]
            return
        
        # Уведомляем всех о ходе
        theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы', 'joker': 'Джокеры'}
        claimed_text = ", ".join([theme_names.get(card, card) for card in claimed_cards])
        
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
        shooter_username = game.get_player_username(result['shooter_id'])
        shoot_messages = [
            f"🔫 {shooter_username} берет револьвер...",
            f"💀 Подносит к виску...",
            f"🎯 Нажимает на курок..."
        ]
        
        for msg in shoot_messages:
            await notify_players(game, context, msg)
            await asyncio.sleep(1.5)
        
        if result['survived']:
            await notify_players(game, context, "✅ ОСЕЧКА!")
            await asyncio.sleep(1)
        else:
            await notify_players(game, context, f"💥 ВЫСТРЕЛ! {shooter_username} выбывает!")
            await asyncio.sleep(3)
        
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
    
    username = next((name for i, pid in enumerate(game.players) if pid == user_id), "Игрок")
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
        "• 4 игрока\n• Каждому по 5 карт\n• Тема: Дамы, Короли или Тузы\n"
        "• Ход: положи 1-3 карты рубашкой вверх\n• Можно обманывать!\n"
        "• Следующий игрок может проверить предыдущего\n"
        "• Если проверка неудачная - русская рулетка\n"
        "• В револьвере 6 патронов, 1 боевой\n• Выбываешь при выстреле\n"
        "• Последний выживший побеждает"
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

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Планируем задачи очистки
    schedule_cleanup_tasks(application)
    
    logger.info("Бот запущен")
    
    # Всегда используем поллинг для простоты
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
