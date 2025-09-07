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
        self.scheduler = schedule.Scheduler()
        self.is_running = False
        self.thread = None
    
    def normalize_url(self, url: str) -> str:
        """
        Агрессивно нормализует URL для максимальной унификации:
        - Убирает схему (http/https)
        - Убирает 'www.'
        - Убирает параметры и фрагменты
        - Убирает конечный слэш
        """
        if not url:
            return ""
        try:
            # Сначала убираем схему для лучшей унификации
            if url.startswith(('http://', 'https://')):
                url = url.split('://', 1)[1]
            
            # Убираем www.
            if url.startswith('www.'):
                url = url.split('www.', 1)[1]

            # Используем urlparse для остального
            parsed = urlparse('http://' + url) # Добавляем временную схему для парсинга
            path = parsed.path.rstrip('/')
            
            # Собираем без схемы
            normalized = f"{parsed.netloc}{path}".lower()
            return normalized
        except Exception as e:
            logger.warning(f"Не удалось нормализовать URL '{url}': {e}")
            return url.lower()

    def check_sources_for_news(self) -> Dict:
        """
        Проверяет все активные источники на наличие новых статей,
        Возвращает словарь с общим количеством и статистикой по источникам.
        """
        try:
            logger.info("Начинаю проверку источников на новые новости...")
            
            sources = self.db.get_news_sources(active_only=True)
            total_new_articles = 0
            articles_by_source = {source['name']: 0 for source in sources}
            
            for source in sources:
                source_name = source['name']
                try:
                    logger.info(f"Проверяю источник: {source_name}")
                    
                    articles = self.scraper.scrape_source(source['source_type'], source['url'])
                    
                    for article in articles:
                        normalized_url = self.normalize_url(article['url'])
                        if not normalized_url:
                            continue

                        if self.db.article_exists(normalized_url):
                            continue
                        
                        article_id = self.db.add_news_article(
                            source['id'],
                            article['title'],
                            article['content'],
                            normalized_url
                        )
                        
                        if article_id:
                            articles_by_source[source_name] += 1
                            total_new_articles += 1
                            logger.info(f"Добавлена новая статья: {article['title'][:50]}...")
                    
                    self.db.update_source_last_check(source['id'])
                    
                except Exception as e:
                    logger.error(f"Ошибка при проверке источника {source_name}: {e}")
            
            logger.info(f"Проверка завершена. Найдено новых статей: {total_new_articles}")
            
            return {
                'total': total_new_articles,
                'by_source': articles_by_source
            }
            
        except Exception as e:
            logger.error(f"Ошибка при проверке источников: {e}")
            return {'total': 0, 'by_source': {}}
    
    def cleanup_old_news_job(self):
        """Задача для очистки старых новостей из БД."""
        logger.info("Запускаю ежедневную задачу очистки старых новостей...")
        self.db.delete_old_articles(days_old=7)

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
            # self.process_pending_articles() # Отключено, т.к. обработка идет при просмотре
            
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
        schedule.every(CHECK_INTERVAL).minutes.do(self.check_sources_for_news)
        schedule.every().day.at("03:00").do(self.cleanup_old_news_job)
        
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
    
    def force_check_sources(self) -> Dict:
        """Принудительная проверка источников и возврат словаря с результатами."""
        logger.info("Запускаю принудительную проверку источников...")
        results = self.check_sources_for_news()
        return results
    
    def force_process_articles(self):
        """Принудительная обработка статей"""
        logger.info("Запускаю принудительную обработку статей...")
        self.process_pending_articles()
