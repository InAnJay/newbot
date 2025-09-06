import schedule
import time
import asyncio
import logging
from datetime import datetime
from typing import List, Dict
import threading
from urllib.parse import urlparse, urlunparse

from database import Database
from news_scraper import NewsScraper
from mistral_client import MistralClient
from openai_client import OpenAIClient
from config import CHECK_INTERVAL

logger = logging.getLogger(__name__)

class NewsScheduler:
    def __init__(self, db: Database, scraper: NewsScraper, mistral: MistralClient, openai: OpenAIClient):
        self.db = db
        self.scraper = scraper
        self.mistral = mistral
        self.openai = openai
        self.is_running = False
        self.thread = None
    
    def normalize_url(self, url: str) -> str:
        """Нормализует URL, убирая параметры, фрагменты и конечный слэш."""
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            # Убираем query params и fragment, убираем конечный слэш из path
            path = parsed.path.rstrip('/')
            # Пересобираем URL в каноническом виде
            normalized = urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))
            return normalized
        except Exception as e:
            logger.warning(f"Не удалось нормализовать URL '{url}': {e}")
            return url

    def check_sources_for_news(self):
        """Проверить все активные источники на новые новости"""
        try:
            logger.info("Начинаю проверку источников на новые новости...")
            
            sources = self.db.get_news_sources(active_only=True)
            new_articles_count = 0
            
            for source in sources:
                try:
                    logger.info(f"Проверяю источник: {source['name']}")
                    
                    # Парсим источник
                    articles = self.scraper.scrape_source(source['source_type'], source['url'])
                    
                    for article in articles:
                        # Нормализуем URL перед проверкой и добавлением
                        normalized_url = self.normalize_url(article['url'])
                        if not normalized_url:
                            continue

                        # Проверяем, не существует ли уже такая статья
                        if self.db.article_exists(normalized_url):
                            continue
                        
                        # Добавляем статью в базу
                        article_id = self.db.add_news_article(
                            source['id'],
                            article['title'],
                            article['content'],
                            normalized_url
                        )
                        
                        if article_id:
                            new_articles_count += 1
                            logger.info(f"Добавлена новая статья: {article['title'][:50]}...")
                    
                    # Обновляем время последней проверки
                    self.db.update_source_last_check(source['id'])
                    
                except Exception as e:
                    logger.error(f"Ошибка при проверке источника {source['name']}: {e}")
            
            logger.info(f"Проверка завершена. Найдено новых статей: {new_articles_count}")
            return new_articles_count
            
        except Exception as e:
            logger.error(f"Ошибка при проверке источников: {e}")
            return 0
    
    def process_pending_articles(self):
        """Обработать статьи в статусе 'pending'"""
        try:
            logger.info("Обрабатываю статьи в ожидании...")
            
            articles = self.db.get_pending_articles()
            
            for article in articles:
                try:
                    # Проверяем, не обработана ли уже статья
                    if article['rewritten_title']:
                        continue
                    
                    logger.info(f"Обрабатываю статью: {article['original_title'][:50]}...")
                    
                    # Переписываем статью
                    rewritten = self.mistral.rewrite_news_article(
                        article['original_title'], 
                        article['original_content']
                    )
                    
                    # Генерируем изображение
                    image_url = self.openai.generate_image(
                        rewritten['title'],
                        rewritten['content']
                    )
                    
                    # Сохраняем в базу
                    self.db.update_article_rewrite(
                        article['id'], 
                        rewritten['title'], 
                        rewritten['content'],
                        rewritten['hashtags']
                    )
                    
                    if image_url:
                        self.db.update_article_image(article['id'], image_url, "")

                    logger.info(f"Статья обработана: {rewritten['title'][:50]}...")
                    
                except Exception as e:
                    logger.error(f"Ошибка при обработке статьи {article['id']}: {e}")
            
        except Exception as e:
            logger.error(f"Ошибка при обработке статей: {e}")
    
    def cleanup_old_articles(self):
        """Очистка старых статей"""
        try:
            logger.info("Очищаю старые статьи...")
            
            # Здесь можно добавить логику для удаления старых статей
            # Например, статьи старше 30 дней в статусе 'rejected'
            
        except Exception as e:
            logger.error(f"Ошибка при очистке старых статей: {e}")
    
    def run_scheduled_tasks(self):
        """Запуск запланированных задач"""
        try:
            logger.info("Запускаю запланированные задачи...")
            
            # Проверяем источники на новые новости
            self.check_sources_for_news()
            
            # Обрабатываем статьи в ожидании
            self.process_pending_articles()
            
            # Очищаем старые статьи
            self.cleanup_old_articles()
            
        except Exception as e:
            logger.error(f"Ошибка при выполнении запланированных задач: {e}")
    
    def start_scheduler(self):
        """Запустить планировщик"""
        if self.is_running:
            logger.warning("Планировщик уже запущен")
            return
        
        logger.info(f"Запускаю планировщик с интервалом {CHECK_INTERVAL} минут")
        
        # Настраиваем расписание
        schedule.every(CHECK_INTERVAL).minutes.do(self.run_scheduled_tasks)
        
        # Запускаем планировщик в отдельном потоке
        self.is_running = True
        self.thread = threading.Thread(target=self._run_scheduler_loop, daemon=True)
        self.thread.start()
        
        logger.info("Планировщик запущен")
    
    def stop_scheduler(self):
        """Остановить планировщик"""
        if not self.is_running:
            logger.warning("Планировщик не запущен")
            return
        
        logger.info("Останавливаю планировщик...")
        
        self.is_running = False
        schedule.clear()
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        logger.info("Планировщик остановлен")
    
    def _run_scheduler_loop(self):
        """Основной цикл планировщика"""
        while self.is_running:
            try:
                schedule.run_pending()
                time.sleep(60)  # Проверяем каждую минуту
            except Exception as e:
                logger.error(f"Ошибка в цикле планировщика: {e}")
                time.sleep(60)
    
    def get_scheduler_status(self) -> Dict:
        """Получить статус планировщика"""
        return {
            'is_running': self.is_running,
            'check_interval': CHECK_INTERVAL,
            'next_run': schedule.next_run() if schedule.jobs else None,
            'jobs_count': len(schedule.jobs)
        }
    
    def force_check_sources(self) -> int:
        """Принудительная проверка источников и возврат количества новых статей."""
        logger.info("Запускаю принудительную проверку источников...")
        count = self.check_sources_for_news()
        return count
    
    def force_process_articles(self):
        """Принудительная обработка статей"""
        logger.info("Запускаю принудительную обработку статей...")
        self.process_pending_articles()
