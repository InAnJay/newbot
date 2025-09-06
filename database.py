import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = "news_bot.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Инициализация базы данных и создание таблиц"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Таблица источников новостей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS news_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL, -- 'rss', 'website', 'telegram'
                    is_active BOOLEAN DEFAULT 1,
                    last_check TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица новостей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS news_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER,
                    original_title TEXT NOT NULL,
                    original_content TEXT,
                    original_url TEXT,
                    rewritten_title TEXT,
                    rewritten_content TEXT,
                    hashtags TEXT,
                    image_url TEXT,
                    image_path TEXT,
                    status TEXT DEFAULT 'pending', -- 'pending', 'approved', 'rejected', 'published'
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    published_at TIMESTAMP,
                    FOREIGN KEY (source_id) REFERENCES news_sources (id)
                )
            ''')
            
            # Проверяем и добавляем колонку hashtags, если ее нет
            cursor.execute("PRAGMA table_info(news_articles)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'hashtags' not in columns:
                logger.info("Обновляю схему БД: добавляю колонку 'hashtags'...")
                cursor.execute("ALTER TABLE news_articles ADD COLUMN hashtags TEXT")
                logger.info("Колонка 'hashtags' успешно добавлена.")
            
            # Очищаем дубликаты статей перед созданием уникального индекса
            logger.info("Проверка и удаление дубликатов статей из БД...")
            cursor.execute('''
                DELETE FROM news_articles
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM news_articles
                    GROUP BY original_url
                )
            ''')
            logger.info("Дубликаты успешно удалены.")

            # Создаем уникальный индекс для URL статей, чтобы избежать дубликатов
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_original_url
                ON news_articles (original_url)
            ''')

            # Таблица настроек бота
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
    
    def add_news_source(self, name: str, url: str, source_type: str) -> int:
        """Добавить новый источник новостей"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO news_sources (name, url, source_type)
                VALUES (?, ?, ?)
            ''', (name, url, source_type))
            return cursor.lastrowid
    
    def get_news_sources(self, active_only: bool = True) -> List[Dict]:
        """Получить список источников новостей"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            query = "SELECT * FROM news_sources"
            if active_only:
                query += " WHERE is_active = 1"
            query += " ORDER BY name"
            
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def get_source_by_id(self, source_id: int) -> Optional[Dict]:
        """Получить источник по ID"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM news_sources WHERE id = ?", (source_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_news_source(self, source_id: int):
        """Удалить источник новостей и связанные с ним статьи."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Сначала удаляем связанные статьи, чтобы избежать ошибок внешнего ключа
            cursor.execute("DELETE FROM news_articles WHERE source_id = ?", (source_id,))
            # Затем удаляем сам источник
            cursor.execute("DELETE FROM news_sources WHERE id = ?", (source_id,))
            conn.commit()
    
    def get_article_by_id(self, article_id: int) -> Optional[Dict]:
        """Получить одну статью по ее ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT na.*, ns.name as source_name
                FROM news_articles na
                LEFT JOIN news_sources ns ON na.source_id = ns.id
                WHERE na.id = ?
            ''', (article_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_source_last_check(self, source_id: int):
        """Обновить время последней проверки источника"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE news_sources 
                SET last_check = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (source_id,))
    
    def article_exists(self, original_url: str) -> bool:
        """Проверить, существует ли статья с таким URL"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM news_articles WHERE original_url = ?", (original_url,))
            return cursor.fetchone() is not None

    def add_news_article(self, source_id: int, original_title: str, 
                        original_content: str, original_url: str) -> Optional[int]:
        """Добавить новую статью, избегая дубликатов на уровне БД."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO news_articles 
                    (source_id, original_title, original_content, original_url)
                    VALUES (?, ?, ?, ?)
                ''', (source_id, original_title, original_content, original_url))
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.warning(f"Попытка добавить дублирующуюся статью (отвергнуто базой данных): {original_url}")
            return None
    
    def get_pending_articles(self) -> List[Dict]:
        """Получить статьи в статусе 'pending'"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT na.*, ns.name as source_name
                FROM news_articles na
                JOIN news_sources ns ON na.source_id = ns.id
                WHERE na.status = 'pending'
                ORDER BY na.created_at DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]
    
    def update_article_rewrite(self, article_id: int, rewritten_title: str, 
                              rewritten_content: str, hashtags: List[str]):
        """Обновить переписанный контент и хэштеги статьи"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            hashtags_json = json.dumps(hashtags, ensure_ascii=False)
            cursor.execute('''
                UPDATE news_articles 
                SET rewritten_title = ?, rewritten_content = ?, hashtags = ?
                WHERE id = ?
            ''', (rewritten_title, rewritten_content, hashtags_json, article_id))
    
    def update_article_image(self, article_id: int, image_url: str, image_path: str):
        """Обновить изображение статьи"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE news_articles 
                SET image_url = ?, image_path = ?
                WHERE id = ?
            ''', (image_url, image_path, article_id))
    
    def update_article_status(self, article_id: int, status: str):
        """Обновить статус статьи"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if status == 'published':
                cursor.execute('''
                    UPDATE news_articles 
                    SET status = ?, published_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, article_id))
            else:
                cursor.execute('''
                    UPDATE news_articles 
                    SET status = ?
                    WHERE id = ?
                ''', (status, article_id))
    
    def get_setting(self, key: str) -> Optional[str]:
        """Получить настройку"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM bot_settings WHERE key = ?', (key,))
            result = cursor.fetchone()
            return result[0] if result else None
    
    def set_setting(self, key: str, value: str):
        """Установить настройку"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (key, value))
