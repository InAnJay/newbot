import logging
from typing import List, Dict, Optional
from telethon import TelegramClient
from telethon.tl.types import Message
from datetime import datetime, timezone

from config import TELEGRAM_API_ID, TELEGRAM_API_HASH

logger = logging.getLogger(__name__)

class TelegramScraperClient:
    """
    Клиент для парсинга Telegram-каналов с использованием Telethon.
    Работает от имени пользователя, используя API ID и HASH.
    """
    def __init__(self, session_name: str = "telegram_session"):
        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
            raise ValueError("TELEGRAM_API_ID и TELEGRAM_API_HASH должны быть установлены в .env файле.")
        
        self.api_id = int(TELEGRAM_API_ID)
        self.api_hash = TELEGRAM_API_HASH
        self.session_name = session_name
        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)

    async def get_channel_messages(self, channel_url: str, limit: int = 20) -> List[Dict]:
        """
        Получает последние сообщения из публичного Telegram-канала.
        """
        articles = []
        try:
            async with self.client:
                entity = await self.client.get_entity(channel_url)
                messages = await self.client.get_messages(entity, limit=limit)
                
                for message in messages:
                    if not message or not message.text:
                        continue
                    
                    # Создаем постоянную ссылку на сообщение
                    message_link = f"https://t.me/{entity.username}/{message.id}"
                    
                    # Форматируем в стандартный вид статьи
                    articles.append({
                        'title': message.text.split('\n')[0][:70], # Первая строка как заголовок
                        'content': message.text,
                        'url': message_link,
                        'published': message.date
                    })

        except Exception as e:
            logger.error(f"Ошибка при получении сообщений из Telegram-канала {channel_url}: {e}")
            logger.error("Убедитесь, что вы прошли аутентификацию при первом запуске (ввод номера телефона/кода в консоли).")

        return articles

    async def test_connection(self):
        """Тестирует соединение с Telegram."""
        try:
            async with self.client:
                me = await self.client.get_me()
                logger.info(f"Успешное подключение к Telegram как {me.username}")
                return True
        except Exception as e:
            logger.error(f"Не удалось подключиться к Telegram: {e}")
            logger.error("Проверьте TELEGRAM_API_ID, TELEGRAM_API_HASH в .env и пройдите аутентификацию.")
            return False
