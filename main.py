import logging
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import random
import string

from config import BOT_TOKEN
from database import Database
from game import LiarsBarGame

logger = logging.getLogger(__name__)

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
            [InlineKeyboardButton("🎮 Создать комнату", callback_data="create_room")],
            [InlineKeyboardButton("📋 Правила игры", callback_data="show_rules")],
            [InlineKeyboardButton("🎯 Присоединиться к игре", callback_data="join_game")]
        ]
        
        await update.message.reply_text(
            f"Привет {user.first_name}! Добро пожаловать в Werb Hub!\n\n"
            "Игра Liar's Bar с русской рулеткой 🎲🔫\n\n"
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
        except Exception as e:
            logger.error(f"Ошибка в callback: {e}")
            await query.edit_message_text("Произошла ошибка. Попробуйте снова.")

    async def create_room(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        
        room_id = ''.join(random.choices(string.digits, k=6))
        
        game = LiarsBarGame(room_id, user_id)
        game.add_player(user_id)
        self.active_games[room_id] = game
        
        await self.db.create_game(room_id, user_id, user_id)
        
        keyboard = [
            [InlineKeyboardButton("✅ Присоединиться", callback_data=f"join_room_{room_id}")],
            [InlineKeyboardButton("🚀 Начать игру", callback_data=f"start_room_{room_id}")],
            [InlineKeyboardButton("📋 Правила", callback_data="show_rules")]
        ]
        
        await query.edit_message_text(
            f"🎮 Комната создана!\n\n"
            f"ID комнаты: {room_id}\n"
            f"Игроков: 1/4\n\n"
            f"Отправь этот ID друзьям или нажми кнопку ниже:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def join_game_prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        
        await query.edit_message_text(
            "Чтобы присоединиться к игре:\n\n"
            "1. Попроси ID комнаты у друга\n"
            "2. Используй команду:\n"
            "/join [ID_комнаты]\n\n"
            "Например: /join 123456"
        )

    async def join_room(self, update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str):
        query = update.callback_query
        user_id = query.from_user.id
        
        game = self.active_games.get(room_id)
        if not game:
            game_data = await self.db.get_game(room_id)
            if game_data:
                game = LiarsBarGame.from_dict(game_data)
                self.active_games[room_id] = game
            else:
                await query.edit_message_text("Комната не найдена")
                return
        
        if user_id in game.players:
            await query.answer("Вы уже в этой комнате")
            return
            
        if len(game.players) >= 4:
            await query.edit_message_text("Комната заполнена")
            return
        
        game.add_player(user_id)
        await self.db.update_game(room_id, game.to_dict())
        
        keyboard = []
        if user_id == game.players[0]:
            keyboard.append([InlineKeyboardButton("🚀 Начать игру", callback_data=f"start_room_{room_id}")])
        
        keyboard.append([InlineKeyboardButton("📋 Правила", callback_data="show_rules")])
        
        await query.edit_message_text(
            f"🎯 Вы присоединились к комнате {room_id}!\n\n"
            f"Игроков: {len(game.players)}/4\n"
            f"Ожидаем начала игры...",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def start_room(self, update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str):
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
                    await context.bot.send_message(
                        chat_id=player_id,
                        text=f"🎮 Игра началась!\n\n"
                             f"Тема раунда: {theme_names.get(game.theme)}\n"
                             f"У тебя на руках 5 карт\n"
                             f"Револьвер заряжен... Удачи! 🔫"
                    )
                except Exception as e:
                    logger.error(f"Не удалось уведомить игрока {player_id}: {e}")
            
            await self.show_game_state(game, context)
        else:
            await query.answer(message)

    async def show_rules(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        
        rules_text = (
            "📋 Правила Liar's Bar:\n\n"
            "👥 4 игрока\n"
            "🃏 Каждому по 5 карт\n"
            "🎯 Тема: Дамы, Короли или Тузы\n"
            "📥 Ход: положи 1-5 карт рубашкой вверх\n"
            "🤥 Можно обманывать!\n"
            "🔍 Следующий игрок может проверить\n"
            "🔫 При неудачной проверке - русская рулетка\n"
            "💀 Выбываешь при выстреле\n"
            "🏆 Последний выживший побеждает!\n\n"
            "Блефуй осторожно! 🎲"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
        await query.edit_message_text(rules_text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def back_to_main(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user = query.from_user
        
        keyboard = [
            [InlineKeyboardButton("🎮 Создать комнату", callback_data="create_room")],
            [InlineKeyboardButton("📋 Правила игры", callback_data="show_rules")],
            [InlineKeyboardButton("🎯 Присоединиться к игре", callback_data="join_game")]
        ]
        
        await query.edit_message_text(
            f"Главное меню\n\n"
            f"Выбери действие:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def play_cards_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE, card_count: int):
        query = update.callback_query
        user_id = query.from_user.id
        
        game = await self.find_user_game(user_id)
        if not game:
            await query.answer("Вы не в активной игре")
            return
        
        success, message = game.play_cards(user_id, card_count)
        if success:
            await self.db.update_game(game.game_id, game.to_dict())
            await self.notify_players(game, context, f"Игрок положил {card_count} карт на стол!")
            await self.show_game_state(game, context)
        else:
            await query.answer(message)

    async def challenge_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            
            shooter_name = "Вы" if shooter_id == user_id else "Игрок"
            
            if survived:
                message = f"💥 {shooter_name} выстрелил и выжил! Продолжаем..."
            else:
                message = f"💀 {shooter_name} выстрелил и выбыл из игры!"
            
            await self.notify_players(game, context, message)
            await self.db.update_game(game.game_id, game.to_dict())
            
            if len(game.players) > 1:
                await self.show_game_state(game, context)
            else:
                await self.notify_players(game, context, f"🎉 Игрок {game.players[0]} победил!")
                del self.active_games[game.game_id]
        else:
            await query.answer(result)

    async def find_user_game(self, user_id: int):
        for game in self.active_games.values():
            if user_id in game.players:
                return game
        return None

    async def notify_players(self, game: LiarsBarGame, context: ContextTypes.DEFAULT_TYPE, message: str):
        for player_id in game.players:
            try:
                await context.bot.send_message(chat_id=player_id, text=message)
            except Exception as e:
                logger.error(f"Не удалось уведомить игрока {player_id}: {e}")

    async def show_game_state(self, game: LiarsBarGame, context: ContextTypes.DEFAULT_TYPE):
        current_player = game.get_current_player()
        theme_names = {'queen': 'Дамы', 'king': 'Короли', 'ace': 'Тузы'}
        
        message = (
            f"🎮 Текущий раунд\n"
            f"Тема: {theme_names.get(game.theme)}\n"
            f"Карт на столе: {len(game.table_cards)}\n"
            f"Игроков осталось: {len(game.players)}\n\n"
            f"Сейчас ходит игрок"
        )
        
        keyboard = [
            [InlineKeyboardButton("🃏 Положить 1 карту", callback_data="play_cards_1")],
            [InlineKeyboardButton("🃏 Положить 2 карты", callback_data="play_cards_2")],
            [InlineKeyboardButton("🃏 Положить 3 карты", callback_data="play_cards_3")],
            [InlineKeyboardButton("🔍 Проверить предыдущего", callback_data="challenge")]
        ]
        
        for player_id in game.players:
            try:
                player_message = message
                if player_id == current_player:
                    player_message += " - ТЫ! 🎯"
                
                await context.bot.send_message(
                    chat_id=player_id,
                    text=player_message,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logger.error(f"Не удалось отправить состояние игры {player_id}: {e}")

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
        
        keyboard = [[InlineKeyboardButton("✅ Присоединиться", callback_data=f"join_room_{room_id}")]]
        
        await update.message.reply_text(
            f"Найдена комната {room_id}\n"
            f"Нажми кнопку чтобы присоединиться:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def main():
    bot = WerbHubBot()
    await bot.init()
    
    application = Application.builder().token(BOT_TOKEN).build()
    bot.setup_handlers(application)
    
    logger.info("Бот запущен на Render!")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
