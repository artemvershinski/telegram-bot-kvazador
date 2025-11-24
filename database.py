import asyncpg
import json
from config import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

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

    async def create_game(self, game_id, chat_id, creator_id):
        await self.pool.execute('''
            INSERT INTO games (game_id, chat_id, players, game_state, theme, table_cards, current_player_index, player_hands, player_revolvers, deck)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ''', game_id, chat_id, json.dumps([creator_id]), 'waiting', None, json.dumps([]), 0, json.dumps({}), json.dumps({}), json.dumps([]))

    async def get_game(self, game_id):
        row = await self.pool.fetchrow('SELECT * FROM games WHERE game_id = $1', game_id)
        if row:
            return {
                'game_id': row['game_id'],
                'chat_id': row['chat_id'],
                'players': json.loads(row['players']),
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
            if key in ['players', 'table_cards', 'player_hands', 'player_revolvers', 'deck']:
                value = json.dumps(value)
            query += f"{key} = ${param_count}, "
            params.append(value)
            param_count += 1
        
        query = query[:-2] + f" WHERE game_id = ${param_count}"
        params.append(game_id)
        await self.pool.execute(query, *params)
