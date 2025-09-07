#!/usr/bin/env python3
"""
Главный файл для запуска бота новостей о маркетплейсах
"""

import logging
import sys
import os
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv

from config import (
    TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, TARGET_CHANNEL_ID,
    MISTRAL_API_KEY, OPENAI_API_KEY,
    TELEGRAM_API_ID, TELEGRAM_API_HASH
)
from database import Database
from news_scraper import NewsScraper
from mistral_client import MistralClient
from openai_client import OpenAIClient
from telegram_client import TelegramScraperClient
from scheduler import NewsScheduler
from telegram_bot import NewsBot

# --- Настройка логирования ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')

# Логирование в файл
file_handler = RotatingFileHandler('news_bot.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# Логирование в консоль
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# Создание корневого логгера
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

logger = logging.getLogger(__name__)


def check_env_vars():
    """Проверяет наличие всех необходимых переменных окружения."""
    required_vars = {
        'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
        'MISTRAL_API_KEY': MISTRAL_API_KEY,
        'OPENAI_API_KEY': OPENAI_API_KEY,
        'ADMIN_USER_ID': ADMIN_USER_ID,
        'TARGET_CHANNEL_ID': TARGET_CHANNEL_ID
    }
    missing_vars = [var for var, value in required_vars.items() if not value]
    if missing_vars:
        error_message = f"Критические переменные окружения не установлены: {', '.join(missing_vars)}. Пожалуйста, заполните файл .env."
        logger.critical(error_message)
        raise ValueError(error_message)

def main():
    """Основная функция для запуска бота."""
    scraper = None  # Инициализируем scraper как None
    try:
        # Переменные окружения уже загружаются в config.py
        logger.info("Инициализация приложения...")

        # Проверка ключевых переменных
        check_env_vars()
        logger.info("Проверка переменных окружения пройдена.")

        # --- 3. Инициализация компонентов ---
        logger.info("Инициализация компонентов...")
        db = Database()
        mistral_client = MistralClient()
        openai_client = OpenAIClient()
        
        telegram_scraper_client = None
        if TELEGRAM_API_ID and TELEGRAM_API_HASH:
            logger.info("Найдены ключи Telegram API. Активирую парсинг Telegram-каналов.")
            telegram_scraper_client = TelegramScraperClient()
        else:
            logger.warning("TELEGRAM_API_ID и TELEGRAM_API_HASH не найдены в .env. Парсинг Telegram-каналов отключен.")
        
        news_scraper = NewsScraper(mistral_client, db, telegram_scraper_client)
        
        scheduler = NewsScheduler(db, news_scraper, mistral_client, openai_client)
        scheduler.start_scheduler()

        # Запуск бота
        logger.info("Запускаю Telegram-бота...")
        bot = NewsBot(db, scheduler, mistral_client, openai_client)
        bot.run()

    except ValueError as ve:
        # Ошибка конфигурации, выводим сообщение и выходим
        logger.error(f"Ошибка конфигурации: {ve}")
        sys.exit(1)
    except Exception as e:
        # Любая другая ошибка при инициализации
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Корректно закрываем Selenium WebDriver при выходе
        if scraper:
            scraper.close()
        logger.info("Приложение завершило работу.")

if __name__ == "__main__":
    main()
