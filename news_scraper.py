import requests
import feedparser
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
import logging
from datetime import datetime, timedelta
import asyncio
import aiohttp
from urllib.parse import urljoin, urlparse
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from config import USER_AGENT, MARKETPLACE_KEYWORDS
from mistral_client import MistralClient

logger = logging.getLogger(__name__)

class NewsScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        self._driver = None
        self.mistral = MistralClient()
        
    def _get_selenium_driver(self):
        """Инициализирует и возвращает Selenium WebDriver."""
        if self._driver is None:
            chrome_options = Options()
            chrome_options.add_argument("--headless")  # Запуск в фоновом режиме
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument(f"user-agent={USER_AGENT}")
            try:
                # Используем встроенный Selenium Manager, который не требует webdriver-manager
                self._driver = webdriver.Chrome(options=chrome_options)
            except Exception as e:
                logger.error(f"Не удалось инициализировать Selenium WebDriver: {e}")
                logger.error("Убедитесь, что Google Chrome установлен в системе.")
                raise
        return self._driver

    def _get_dynamic_page_source(self, url: str) -> str:
        """Получает HTML-код страницы после выполнения JavaScript."""
        try:
            driver = self._get_selenium_driver()
            driver.get(url)
            # Даем время на прогрузку JS
            time.sleep(5) 
            return driver.page_source
        except Exception as e:
            logger.error(f"Ошибка при получении динамического HTML с {url}: {e}")
            return ""

    def is_marketplace_related(self, text: str) -> bool:
        """Проверить, относится ли текст к маркетплейсам"""
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in MARKETPLACE_KEYWORDS)
    
    def _parse_shoppers_media(self, soup: BeautifulSoup, base_url: str) -> List[Dict]:
        """Специализированный парсер для shoppers.media."""
        articles = []
        # Ищем основной контейнер для новостей
        news_container = soup.find('div', class_='infinite-container')
        if not news_container:
            return []
        
        # Находим все карточки новостей
        news_cards = news_container.find_all('div', class_='news-card')
        
        for card in news_cards:
            title_element = card.find('div', class_='news-card__title')
            link_element = card.find('a', class_='news-card__link')
            subtitle_element = card.find('div', class_='news-card__subtitle')

            if title_element and link_element and link_element.has_attr('href'):
                title = title_element.get_text(strip=True)
                url = urljoin(base_url, link_element['href'])
                # Используем подзаголовок как основной контент, если он есть
                content = subtitle_element.get_text(strip=True) if subtitle_element else ''

                # Проверяем релевантность, хотя на странице тега это может быть излишним
                if title and self.is_marketplace_related(title + ' ' + content):
                    articles.append({
                        'title': self.clean_text(title),
                        'content': self.clean_text(content),
                        'url': url,
                        'published': None
                    })
        return articles

    def clean_text(self, text: str) -> str:
        """Очистить текст от лишних символов"""
        if not text:
            return ""
        
        # Удаляем HTML теги
        text = re.sub(r'<[^>]+>', '', text)
        # Удаляем лишние пробелы и переносы строк
        text = re.sub(r'\s+', ' ', text)
        # Удаляем специальные символы
        text = re.sub(r'[^\w\s\.\,\!\?\:\-\(\)]', '', text)
        
        return text.strip()
    
    def extract_text_from_html(self, html: str) -> str:
        """Извлечь текст из HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        
        # Удаляем скрипты и стили
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Получаем текст
        text = soup.get_text()
        return self.clean_text(text)
    
    def scrape_website_with_gpt(self, url: str) -> List[Dict]:
        """Использует Selenium для получения HTML и GPT для его анализа."""
        logger.info(f"Использую GPT для анализа сайта: {url}")
        html_content = self._get_dynamic_page_source(url)
        if not html_content:
            return []

        mistral_articles = self.mistral.find_articles_on_page(html_content, url)
        
        articles = []
        for article_data in mistral_articles:
            # Преобразуем результат от GPT в наш стандартный формат
            articles.append({
                'title': self.clean_text(article_data.get('title', '')),
                'content': self.clean_text(article_data.get('summary', '')),
                'url': article_data.get('url', url),
                'published': None
            })
        
        return articles

    def scrape_rss_feed(self, url: str) -> List[Dict]:
        """Парсинг RSS ленты"""
        try:
            feed = feedparser.parse(url)
            articles = []
            
            for entry in feed.entries:
                # Проверяем, что статья свежая (не старше 24 часов)
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_date = datetime(*entry.published_parsed[:6])
                    if datetime.now() - pub_date > timedelta(hours=24):
                        continue
                
                title = entry.get('title', '')
                content = entry.get('summary', '') or entry.get('description', '')
                link = entry.get('link', '')
                
                # Проверяем релевантность
                if self.is_marketplace_related(title + ' ' + content):
                    articles.append({
                        'title': self.clean_text(title),
                        'content': self.clean_text(content),
                        'url': link,
                        'published': pub_date if hasattr(entry, 'published_parsed') and entry.published_parsed else None
                    })
            
            return articles
            
        except Exception as e:
            logger.error(f"Ошибка при парсинге RSS {url}: {e}")
            return []
    
    def scrape_website(self, url: str) -> List[Dict]:
        """Парсинг обычного веб-сайта"""
        try:
            # Для сайтов, требующих JS, используем новый метод с GPT
            if 'shoppers.media' in url:
                return self.scrape_website_with_gpt(url)

            # Для статических сайтов используем requests
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles = []
            
            # Ищем статьи по различным селекторам
            article_selectors = [
                'article',
                '.article',
                '.news-item',
                '.post',
                '.entry',
                '[class*="article"]',
                '[class*="news"]',
                '[class*="post"]'
            ]
            
            for selector in article_selectors:
                elements = soup.select(selector)
                for element in elements:
                    # Извлекаем заголовок
                    title_elem = element.find(['h1', 'h2', 'h3', '.title', '.headline'])
                    title = title_elem.get_text().strip() if title_elem else ""
                    
                    # Извлекаем контент
                    content_elem = element.find(['p', '.content', '.text', '.description'])
                    content = content_elem.get_text().strip() if content_elem else ""
                    
                    # Извлекаем ссылку
                    link_elem = element.find('a', href=True)
                    link = urljoin(url, link_elem['href']) if link_elem else url
                    
                    # Проверяем релевантность
                    if title and self.is_marketplace_related(title + ' ' + content):
                        articles.append({
                            'title': self.clean_text(title),
                            'content': self.clean_text(content),
                            'url': link,
                            'published': None
                        })
            
            return articles
            
        except Exception as e:
            logger.error(f"Ошибка при парсинге сайта {url}: {e}")
            return []
    
    def scrape_telegram_channel(self, channel_url: str) -> List[Dict]:
        """Парсинг Telegram канала (базовая реализация)"""
        # Для полноценного парсинга Telegram нужен Telegram API
        # Здесь базовая реализация для демонстрации
        try:
            # В реальном проекте здесь будет интеграция с Telegram API
            # или использование веб-версии Telegram
            logger.info(f"Парсинг Telegram канала: {channel_url}")
            return []
            
        except Exception as e:
            logger.error(f"Ошибка при парсинге Telegram канала {channel_url}: {e}")
            return []
    
    def scrape_source(self, source_type: str, url: str) -> List[Dict]:
        """Парсинг источника в зависимости от его типа"""
        if source_type == 'rss':
            return self.scrape_rss_feed(url)
        elif source_type == 'website':
            return self.scrape_website(url)
        elif source_type == 'telegram':
            return self.scrape_telegram_channel(url)
        else:
            logger.warning(f"Неизвестный тип источника: {source_type}")
            return []
    
    async def scrape_multiple_sources(self, sources: List[Dict]) -> List[Dict]:
        """Асинхронный парсинг нескольких источников"""
        async with aiohttp.ClientSession() as session:
            tasks = []
            for source in sources:
                task = self.scrape_source_async(session, source)
                tasks.append(task)
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            all_articles = []
            for result in results:
                if isinstance(result, list):
                    all_articles.extend(result)
                elif isinstance(result, Exception):
                    logger.error(f"Ошибка при парсинге: {result}")
            
            return all_articles
    
    async def scrape_source_async(self, session: aiohttp.ClientSession, source: Dict) -> List[Dict]:
        """Асинхронный парсинг одного источника"""
        try:
            source_type = source['source_type']
            url = source['url']
            
            if source_type == 'rss':
                return self.scrape_rss_feed(url)
            elif source_type == 'website':
                async with session.get(url) as response:
                    if response.status == 200:
                        html = await response.text()
                        return self.scrape_website_content(html, url)
            elif source_type == 'telegram':
                return self.scrape_telegram_channel(url)
            
            return []
            
        except Exception as e:
            logger.error(f"Ошибка при асинхронном парсинге {source.get('url', 'unknown')}: {e}")
            return []
    
    def scrape_website_content(self, html: str, base_url: str) -> List[Dict]:
        """Парсинг контента веб-сайта из HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        articles = []
        
        # Ищем статьи по различным селекторам
        article_selectors = [
            'article',
            '.article',
            '.news-item',
            '.post',
            '.entry',
            '[class*="article"]',
            '[class*="news"]',
            '[class*="post"]'
        ]
        
        for selector in article_selectors:
            elements = soup.select(selector)
            for element in elements:
                # Извлекаем заголовок
                title_elem = element.find(['h1', 'h2', 'h3', '.title', '.headline'])
                title = title_elem.get_text().strip() if title_elem else ""
                
                # Извлекаем контент
                content_elem = element.find(['p', '.content', '.text', '.description'])
                content = content_elem.get_text().strip() if content_elem else ""
                
                # Извлекаем ссылку
                link_elem = element.find('a', href=True)
                link = urljoin(base_url, link_elem['href']) if link_elem else base_url
                
                # Проверяем релевантность
                if title and self.is_marketplace_related(title + ' ' + content):
                    articles.append({
                        'title': self.clean_text(title),
                        'content': self.clean_text(content),
                        'url': link,
                        'published': None
                    })
        
        return articles

    def __del__(self):
        """Закрывает драйвер при уничтожении объекта."""
        if self._driver:
            self._driver.quit()
