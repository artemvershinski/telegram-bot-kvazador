import logging
import os
import asyncio
import json
import random
import string
from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import asyncpg

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не установлен")

# ========== DATABASE КЛАСС ==========
class Database:
    def __init__(self):
        self.pool = None

    async def init(self):
        try:
            self.pool = await asyncpg.create_pool(DATABASE_URL)
            await self.create_tables()
            logger.info("База данных подключена")
        except Exception as e:
            logger.error(f"Ошибка подключения к базе: {e}")

    async def create_tables(self):
        await self.pool.execute('''
            CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY,
                chat_id BIGINT,
                players JSONB,
                player_usernames JSONB,
                game_state TEXT,
                theme TEXT,
                table_cards JSONB,
                current_player_index INTEGER,
                player_hands JSONB,
                player_revolvers JSONB,
                deck JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')

    async def create_game(self, game_id, creator_id, creator_username):
        await self.pool.execute('''
            INSERT INTO games (game_id, chat_id, players, player_usernames, game_state, theme, table_cards, current_player_index, player_hands, player_revolvers, deck)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ''', game_id, creator_id, json.dumps([creator_id]), json.dumps([creator_username]), 'waiting', None, json.dumps([]), 0, json.dumps({}), json.dumps({}), json.dumps([]))

    async def get_game(self, game_id):
        row = await self.pool.fetchrow('SELECT * FROM games WHERE game_id = $1', game_id)
        if row:
            return {
                'game_id': row['game_id'],
                'chat_id': row['chat_id'],
                'players': json.loads(row['players']),
                'player_usernames': json.loads(row['player_usernames']),
                'game_state': row['game_state'],
                'theme': row['theme'],
                'table_cards': json.loads(row['table_cards']),
                'current_player_index': row['current_player_index'],
                'player_hands': json.loads(row['player_hands']),
                'player_revolvers': json.loads(row['player_revolvers']),
                'deck': json.loads(row['deck'])
            }
        return None

    async def update_game(self, game_id, updates):
        query = 'UPDATE games SET '
        params = []
        param_count = 1
        
        for key, value in updates.items():
            if key in ['players', 'player_usernames', 'table_cards', 'player_hands', 'player_revolvers', 'deck']:
                value = json.dumps(value)
            query += f"{key} = ${param_count}, "
            params.append(value)
            param_count += 1
        
        query = query[:-2] + f" WHERE game_id = ${param_count}"
        params.append(game_id)
        await self.pool.execute(query, *params)

# ========== GAME КЛАСС ==========
class LiarsBarGame:
    def __init__(self, game_id: str, creator_id: int):
        self.game_id = game_id
        self.creator_id = creator_id
        self.players = []
        self.player_usernames = []
        self.game_state = "waiting"
        self.theme = None
        self.table_cards = []
        self.current_player_index = 0
        self.player_hands = {}
        self.player_revolvers = {}
        self.deck = []
        
        self.create_deck()
    
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
        
        for player_id in self.players:
            self.player_revolvers[player_id] = {
                'chamber': random.randint(0, 5),
                'current_position': 0
            }
        
        self.theme = random.choice(['queen', 'king', 'ace'])
        self.deal_cards()
        
        return True, "Игра началась"
    
    def deal_cards(self):
        self.create_deck()
        random.shuffle(self.deck)
        cards_per_player = 5
        
        self.player_hands = {}
        
        for i, player_id in enumerate(self.players):
            start_index = i * cards_per_player
            end_index = start_index + cards_per_player
            self.player_hands[player_id] = self.deck[start_index:end_index]
    
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
        
        return True, f"Положил {card_count} карт на стол"
    
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
            self.deal_cards()
            self.table_cards = []
            self.current_player_index = 0
    
        return True, {
            'shooter': shooter_id,
            'survived': result,
            'had_theme_cards': has_theme_cards
        }
    
    def fire_revolver(self, player_id: int):
        revolver = self.player_revolvers[player_id]
        
        if revolver['current_position'] == revolver['chamber']:
            # Находим индекс перед удалением
            index = self.players.index(player_id)
            self.players.remove(player_id)
            self.player_usernames.pop(index)
            return False
        else:
            revolver['current_position'] = (revolver['current_position'] + 1) % 6
            return True
    
    def get_current_player(self):
        return self.players[self.current_player_index]
    
    def to_dict(self):
        return {
            'game_id': self.game_id,
            'chat_id': self.creator_id,
            'players': self.players,
            'player_usernames': self.player_usernames,
            'game_state': self.game_state,
            'theme': self.theme,
            'table_cards': self.table_cards,
            'current_player_index': self.current_player_index,
            'player_hands': self.player_hands,
            'player_revolvers': self.player_revolvers,
            'deck': self.deck
        }
    
    @classmethod
    def from_dict(cls, data):
        game = cls(data['game_id'], data['chat_id'])
        game.players = data['players']
        game.player_usernames = data['player_usernames']
        game.game_state = data['game_state']
        game.theme = data['theme']
        game.table_cards = data['table_cards']
        game.current_player_index = data['current_player_index']
        game.player_hands = data['player_hands']
        game.player_revolvers = data['player_revolvers']
        game.deck = data['deck']
        return game

# ========== BOT КЛАСС ==========
class WerbHubBot:
    def __init__(self):
        self.db = Database()
        self.active_games = {}

    async def init(self):
        await self.db.init()
        logger.info("Бот инициализирован")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("Создать комнату", callback_data="create_room")],
            [InlineKeyboardButton("Правила игры", callback_data="show_rules")],
            [InlineKeyboardButton("Присоединиться к игре", callback_data="join_game")]
        ]
        
        await update.message.reply_text(
            f"Привет {user.first_name}.\n\n"
            "Werb Hub - Liar's Bar с русской рулеткой.\n\n"
            "Выбери действие:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        try:
            if data == "create_room":
                await self.create_room(update, context)
            elif data == "show_rules":
                await self.show_rules(update, context)
            elif data == "join_game":
                await self.join_game_prompt(update, context)
            elif data == "back_to_main":
                await self.back_to_main(update, context)
            elif data.startswith("join_room_"):
                room_id = data.split("_")[2]
                await self.join_room(update, context, room_id)
            elif data.startswith("start_room_"):
                room_id = data.split("_")[2]
                await self.start_room(update, context, room_id)
            elif data.startswith("play_cards_"):
                card_count = int(data.split("_")[2])
                await self.play_cards_handler(update, context, card_count)
            elif data == "challenge":
                await self.challenge_handler(update, context)
            elif data.startswith("leave_room_"):
                room_id = data.split("_")[2]
                await self.leave_room(update, context, room_id)
        except Exception as e:
            logger.error(f"Ошибка в callback: {e}")
            await query.edit_message_text("Ошибка. Попробуйте снова.")

    async def create_room(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            user_id = query.from_user.id
            username = query.from_user.username or query.from_user.first_name
            
            room_id = ''.join(random.choices(string.digits, k=6))
            
            game = LiarsBarGame(room_id, user_id)
            game.add_player(user_id, f"@{username}")
            self.active_games[room_id] = game
            
            await self.db.create_game(room_id, user_id, f"@{username}")
            
            players_text = "\n".join([f"• {username}" for username in game.player_usernames])
            
            keyboard = [
                [InlineKeyboardButton("Присоединиться", callback_data=f"join_room_{room_id}")],
                [InlineKeyboardButton("Начать игру", callback_data=f"start_room_{room_id}")],
                [InlineKeyboardButton("Выйти", callback_data=f"leave_room_{room_id}")]
            ]
            
            await query.edit_message_text(
                f"Комната создана\n\n"
                f"ID комнаты: {room_id}\n"
                f"Игроков: {len(game.players)}/4\n\n"
                f"Игроки:\n{players_text}\n\n"
                f"Отправь этот ID друзьям:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Ошибка при создании комнаты: {e}")
            await update.callback_query.edit_message_text("Ошибка при создании комнаты. Попробуйте снова.")

    async def join_room(self, update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str):
        try:
            query = update.callback_query
            user_id = query.from_user.id
            username = query.from_user.username or query.from_user.first_name
            
            game = self.active_games.get(room_id)
            if not game:
                game_data = await self.db.get_game(room_id)
                if game_data:
                    game = LiarsBarGame.from_dict(game_data)
                    self.active_games[room_id] = game
                else:
                    await query.answer("Комната не найдена")
                    return
            
            if user_id in game.players:
                await query.answer("Вы уже в этой комнате")
                return
                
            if len(game.players) >= 4:
                await query.answer("Комната заполнена")
                return
            
            game.add_player(user_id, f"@{username}")
            await self.db.update_game(room_id, game.to_dict())
            
            # УВЕДОМЛЕНИЕ ДЛЯ ВСЕХ ИГРОКОВ
            await self.notify_players(game, context, f"@{username} присоединился к комнате")
            
            # Обновляем сообщение у всех игроков
            await self.update_room_for_all_players(game, context)
            
            await query.answer("Вы присоединились к комнате")
            
        except Exception as e:
            logger.error(f"Ошибка при присоединении к комнате: {e}")
            await update.callback_query.answer("Ошибка при присоединении")

    async def leave_room(self, update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str):
        try:
            query = update.callback_query
            user_id = query.from_user.id
            
            game = self.active_games.get(room_id)
            if not game:
                await query.answer("Комната не найдена")
                return
            
            if user_id not in game.players:
                await query.answer("Вы не в этой комнате")
                return
            
            username = next((name for i, pid in enumerate(game.players) if pid == user_id), "Игрок")
            game.remove_player(user_id)
            await self.db.update_game(room_id, game.to_dict())
            
            # УВЕДОМЛЕНИЕ ДЛЯ ВСЕХ ИГРОКОВ
            await self.notify_players(game, context, f"{username} вышел из комнаты")
            
            # Если комната пустая, удаляем ее
            if len(game.players) == 0:
                del self.active_games[room_id]
                await query.edit_message_text("Вы вышли из комнаты. Комната удалена.")
            else:
                # Обновляем сообщение у оставшихся игроков
                await self.update_room_for_all_players(game, context)
                await query.edit_message_text("Вы вышли из комнаты")
                
        except Exception as e:
            logger.error(f"Ошибка при выходе из комнаты: {e}")
            await update.callback_query.answer("Ошибка при выходе")

    async def update_room_for_all_players(self, game: LiarsBarGame, context: ContextTypes.DEFAULT_TYPE):
        """Обновляет сообщение комнаты у всех игроков"""
        try:
            players_text = "\n".join([f"• {username}" for username in game.player_usernames])
            
            for player_id in game.players:
                try:
                    keyboard = []
                    if player_id == game.players[0]:  # создатель
                        keyboard.append([InlineKeyboardButton("Начать игру", callback_data=f"start_room_{game.game_id}")])
                    
                    keyboard.extend([
                        [InlineKeyboardButton("Присоединиться", callback_data=f"join_room_{game.game_id}")],
                        [InlineKeyboardButton("Выйти", callback_data=f"leave_room_{game.game_id}")]
                    ])
                    
                    await context.bot.send_message(
                        chat_id=player_id,
                        text=f"Комната {game.game_id}\n\n"
                             f"Игроков: {len(game.players)}/4\n\n"
                             f"Игроки:\n{players_text}\n\n"
                             f"Ожидаем начала игры...",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception as e:
                    logger.error(f"Не удалось обновить комнату для игрока {player_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка в update_room_for_all_players: {e}")

    async def start_room(self, update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str):
        try:
            query = update.callback_query
            user_id = query.from_user.id
            
            game = self.active_games.get(room_id)
            if not game:
                await query.answer("Комната не найдена")
                return
            
            if game.players[0] != user_id:
                await query.answer("Только создатель комнаты может начать игру")
                return
            
            if len(game.players) < 4:
                await query.answer("Нужно 4 игрока для начала игры")
                return
            
            success, message = game.start_game()
            if success:
                await self.db.update_game(room_id, game.to_dict())
                
                theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы'}
                for player_id in game.players:
                    try:
                        hand = game.player_hands.get(player_id, [])
                        hand_text = ", ".join(hand)
                        
                        await context.bot.send_message(
                            chat_id=player_id,
                            text=f"Игра началась\n\n"
                                 f"Тема раунда: {theme_names.get(game.theme)}\n"
                                 f"Твои карты: {hand_text}\n"
                                 f"Револьвер заряжен"
                        )
                    except Exception as e:
                        logger.error(f"Не удалось уведомить игрока {player_id}: {e}")
                
                await self.show_game_state(game, context)
            else:
                await query.answer(message)
        except Exception as e:
            logger.error(f"Ошибка при старте игры: {e}")
            await update.callback_query.answer("Ошибка при старте игры")

    async def show_rules(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        
        rules_text = (
            "Правила Liar's Bar:\n\n"
            "• 4 игрока\n"
            "• Каждому по 5 карт\n"
            "• Тема: Дамы, Короли или Тузы\n"
            "• Ход: положи 1-3 карты рубашкой вверх\n"
            "• Можно обманывать о том, какие карты кладешь\n"
            "• Следующий игрок может проверить предыдущего\n"
            "• Если проверка неудачная - русская рулетка\n"
            "• В револьвере 6 патронов, 1 боевой\n"
            "• Выбываешь при выстреле\n"
            "• Последний выживший побеждает"
        )
        
        keyboard = [[InlineKeyboardButton("Назад", callback_data="back_to_main")]]
        await query.edit_message_text(rules_text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def back_to_main(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = query.from_user
        
        keyboard = [
            [InlineKeyboardButton("Создать комнату", callback_data="create_room")],
            [InlineKeyboardButton("Правила игры", callback_data="show_rules")],
            [InlineKeyboardButton("Присоединиться к игре", callback_data="join_game")]
        ]
        
        await query.edit_message_text(
            f"Главное меню\n\n"
            f"Выбери действие:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def play_cards_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE, card_count: int):
        try:
            query = update.callback_query
            user_id = query.from_user.id
            
            game = await self.find_user_game(user_id)
            if not game:
                await query.answer("Вы не в активной игре")
                return
            
            success, message = game.play_cards(user_id, card_count)
            if success:
                await self.db.update_game(game.game_id, game.to_dict())
                await self.notify_players(game, context, f"Игрок положил {card_count} карт на стол")
                
                if "ПОБЕДА" in message:
                    await self.notify_players(game, context, f"Игрок победил!")
                    del self.active_games[game.game_id]
                else:
                    await self.show_game_state(game, context)
            else:
                await query.answer(message)
        except Exception as e:
            logger.error(f"Ошибка в play_cards_handler: {e}")
            await update.callback_query.answer("Ошибка при ходе")

    async def challenge_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            query = update.callback_query
            user_id = query.from_user.id
            
            game = await self.find_user_game(user_id)
            if not game:
                await query.answer("Вы не в активной игре")
                return
            
            success, result = game.challenge_previous_player(user_id)
            if success:
                shooter_id = result['shooter']
                survived = result['survived']
                
                shooter_username = next((username for i, player_id in enumerate(game.players) if player_id == shooter_id), "Игрок")
                
                if survived:
                    message = f"{shooter_username} выстрелил и выжил"
                else:
                    message = f"{shooter_username} выстрелил и выбыл из игры"
                
                await self.notify_players(game, context, message)
                await self.db.update_game(game.game_id, game.to_dict())
                
                if len(game.players) > 1:
                    await self.show_game_state(game, context)
                else:
                    await self.notify_players(game, context, f"{game.player_usernames[0]} победил")
                    del self.active_games[game.game_id]
            else:
                await query.answer(result)
        except Exception as e:
            logger.error(f"Ошибка в challenge_handler: {e}")
            await update.callback_query.answer("Ошибка при проверке")

    async def find_user_game(self, user_id: int):
        for game in self.active_games.values():
            if user_id in game.players:
                return game
        return None

    async def notify_players(self, game: LiarsBarGame, context: ContextTypes.DEFAULT_TYPE, message: str):
        """Отправляет уведомление всем игрокам в комнате"""
        for player_id in game.players:
            try:
                await context.bot.send_message(chat_id=player_id, text=message)
            except Exception as e:
                logger.error(f"Не удалось уведомить игрока {player_id}: {e}")

    async def show_game_state(self, game: LiarsBarGame, context: ContextTypes.DEFAULT_TYPE):
        try:
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
                        f"Игроков осталось: {len(game.players)}\n\n"
                    )
                    
                    if player_id == current_player:
                        message += "Сейчас твой ход"
                        keyboard = [
                            [InlineKeyboardButton("Положить 1 карту", callback_data="play_cards_1")],
                            [InlineKeyboardButton("Положить 2 карты", callback_data="play_cards_2")],
                            [InlineKeyboardButton("Положить 3 карты", callback_data="play_cards_3")],
                            [InlineKeyboardButton("Проверить предыдущего", callback_data="challenge")]
                        ]
                    else:
                        current_username = next((username for i, pid in enumerate(game.players) if pid == current_player), "Игрок")
                        message += f"Сейчас ходит {current_username}"
                        keyboard = [[InlineKeyboardButton("Проверить предыдущего", callback_data="challenge")]]
                    
                    await context.bot.send_message(
                        chat_id=player_id,
                        text=message,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception as e:
                    logger.error(f"Не удалось отправить состояние игры {player_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка в show_game_state: {e}")

    async def join_game_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        
        await query.edit_message_text(
            "Чтобы присоединиться к игре:\n\n"
            "1. Попроси ID комнаты у друга\n"
            "2. Используй команду:\n"
            "/join [ID_комнаты]\n\n"
            "Например: /join 123456"
        )

    def setup_handlers(self, application):
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("join", self.join_command))
        application.add_handler(CallbackQueryHandler(self.handle_callback))

    async def join_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "Укажи ID комнаты:\n"
                "/join 123456"
            )
            return
        
        room_id = context.args[0]
        
        keyboard = [[InlineKeyboardButton("Присоединиться", callback_data=f"join_room_{room_id}")]]
        
        await update.message.reply_text(
            f"Найдена комната {room_id}\n"
            f"Нажми кнопку чтобы присоединиться:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

def main():
    bot = WerbHubBot()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(bot.init())
        
        application = Application.builder().token(BOT_TOKEN).build()
        bot.setup_handlers(application)
        
        logger.info("Бот запущен")
        application.run_polling()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
    finally:
        loop.close()

if __name__ == "__main__":
    main()
