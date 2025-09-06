import os
from dotenv import load_dotenv

load_dotenv()

# Telegram настройки
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 0))
TARGET_CHANNEL_ID = os.getenv('TARGET_CHANNEL_ID')


# Mistral AI настройки
MISTRAL_API_KEY = os.getenv('MISTRAL_API_KEY')

# OpenAI настройки (только для изображений)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# База данных
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///news_bot.db')

# Планировщик
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 60))  # в минутах

# Настройки для парсинга
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

# Ключевые слова для поиска новостей о маркетплейсах
MARKETPLACE_KEYWORDS = [
    'маркетплейс', 'marketplace', 'ozon', 'wildberries', 'яндекс маркет', 
    'aliexpress', 'amazon', 'ebay', 'lamoda', 'beru', 'avito', 'youla',
    'онлайн-торговля', 'e-commerce', 'интернет-магазин', 'доставка',
    'логистика', 'склад', 'товар', 'продажи', 'комиссия', 'комиссия маркетплейса'
]

# Настройки для генерации изображений
IMAGE_SIZE = "1024x1024" # Размер для DALL-E 3
IMAGE_QUALITY = "standard" # standard или hd для DALL-E 3
