import os
import logging

BOT_TOKEN = os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не установлен")
