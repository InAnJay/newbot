import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram Bot ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID")) if os.getenv("ADMIN_USER_ID") else None
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")

# --- Telegram User API (для парсинга каналов) ---
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# --- AI APIs ---
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# База данных
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///news_bot.db')
DB_PATH = DATABASE_URL.split('sqlite:///')[-1] if DATABASE_URL.startswith('sqlite:///') else 'news_bot.db'

# Начальный список ключевых слов для заполнения БД
INITIAL_KEYWORDS = [
    'маркетплейс', 'marketplace', 'ozon', 'wildberries', 'яндекс маркет', 
    'aliexpress', 'amazon', 'ebay', 'lamoda', 'beru', 'avito', 'youla',
    'онлайн-торговля', 'e-commerce', 'интернет-магазин', 'доставка',
    'логистика', 'склад', 'товар', 'продажи', 'комиссия'
]

# Планировщик
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 60))  # в минутах

# Настройки для парсинга
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

# Настройки для генерации изображений
IMAGE_SIZE = "1024x1024" # Размер для DALL-E 3
IMAGE_QUALITY = "standard" # standard или hd для DALL-E 3
