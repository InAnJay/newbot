import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import logging
from urllib.parse import urlparse

from config import DB_PATH, INITIAL_KEYWORDS

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.init_database()
    
    def _normalize_url_aggressive(self, url: str) -> str:
        """Агрессивно нормализует URL для максимальной унификации."""
        if not url:
            return ""
        try:
            # Сначала убираем схему для лучшей унификации
            if url.startswith(('http://', 'https://')):
                url = url.split('://', 1)[1]
            if url.startswith('www.'):
                url = url.split('www.', 1)[1]
            parsed = urlparse('http://' + url)
            path = parsed.path.rstrip('/')
            normalized = f"{parsed.netloc}{path}".lower()
            return normalized
        except Exception:
            return url.lower() # Возвращаем хоть что-то в случае ошибки

    def _cleanup_and_migrate(self, conn):
        """
        Выполняет все операции по очистке и миграции базы данных в рамках одной транзакции.
        Этот метод должен вызываться один раз при инициализации.
        """
        cursor = conn.cursor()
        logger.info("Запуск процесса очистки и миграции базы данных...")

        # 1. Миграция: Добавление колонки hashtags (если нужно)
        cursor.execute("PRAGMA table_info(news_articles)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'hashtags' not in columns:
            logger.info("Миграция: Добавление колонки 'hashtags'...")
            cursor.execute("ALTER TABLE news_articles ADD COLUMN hashtags TEXT")
            logger.info("Колонка 'hashtags' успешно добавлена.")

        # 2. Миграция: Нормализация ВСЕХ существующих URL
        logger.info("Миграция: Нормализация существующих URL...")
        cursor.execute("SELECT id, original_url FROM news_articles")
        updates = [(self._normalize_url_aggressive(url), article_id) for article_id, url in cursor.fetchall()]
        if updates:
            cursor.executemany("UPDATE news_articles SET original_url = ? WHERE id = ?", updates)
            logger.info(f"Нормализовано {len(updates)} URL.")

        # 3. Очистка: Удаление дубликатов ПОСЛЕ нормализации
        logger.info("Очистка: Удаление дубликатов...")
        cursor.execute('''
            DELETE FROM news_articles
            WHERE id NOT IN (
                SELECT MIN(id) FROM news_articles GROUP BY original_url
            )
        ''')
        if cursor.rowcount > 0:
            logger.info(f"Удалено {cursor.rowcount} дубликатов статей.")

        # 4. Установка UNIQUE индекса для предотвращения будущих дублей
        logger.info("Создание UNIQUE индекса для `original_url`...")
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_original_url ON news_articles (original_url)')
        
        conn.commit()
        cursor.close()
        logger.info("Процесс очистки и миграции базы данных завершен.")


    def init_database(self):
        """Инициализирует базу данных, создает таблицы и запускает очистку."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Создание всех таблиц...
            # (код создания таблиц news_sources, news_articles, bot_settings, keywords)
            # ...
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            cursor.close()

        # Выполняем очистку и миграцию в отдельном соединении, чтобы гарантировать commit
        with sqlite3.connect(self.db_path) as conn:
            self._cleanup_and_migrate(conn)

        self.seed_initial_keywords()

    def seed_initial_keywords(self):
        """Заполняет таблицу ключевых слов начальными данными из конфига."""
        current_keywords = self.get_keywords()
        if not current_keywords and INITIAL_KEYWORDS:
            logger.info("База данных ключевых слов пуста. Заполняю начальными значениями...")
            for keyword in INITIAL_KEYWORDS:
                self.add_keyword(keyword)
            logger.info(f"Добавлено {len(INITIAL_KEYWORDS)} ключевых слов в базу.")

    def add_news_source(self, name: str, url: str, source_type: str) -> int:
        """Добавить новый источник новостей"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO news_sources (name, url, source_type)
                    VALUES (?, ?, ?)
                ''', (name, url, source_type))
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.warning(f"Попытка добавить дублирующийся источник: {url}")
            raise ValueError("Этот URL уже существует в списке источников.")
    
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

    def update_source_details(self, source_id: int, name: Optional[str] = None, url: Optional[str] = None) -> bool:
        """Обновляет название и/или URL источника."""
        if not name and not url:
            return False

        query_parts = []
        params = []

        if name:
            query_parts.append("name = ?")
            params.append(name)
        
        if url:
            query_parts.append("url = ?")
            params.append(url)
        
        params.append(source_id)
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                query = f"UPDATE news_sources SET {', '.join(query_parts)} WHERE id = ?"
                cursor.execute(query, tuple(params))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            logger.warning(f"Ошибка обновления: URL '{url}' уже существует.")
            raise ValueError(f"URL '{url}' уже используется другим источником.")

    def delete_news_source(self, source_id: int):
        """Удалить источник новостей и связанные с ним статьи."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Статьи удалятся автоматически благодаря ON DELETE CASCADE
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
    
    def get_pending_articles_paginated(self, page: int = 1, page_size: int = 15) -> (List[Dict], int):
        """Получить статьи в статусе 'pending' с пагинацией."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Сначала считаем общее количество для пагинации
            cursor.execute("SELECT COUNT(*) FROM news_articles WHERE status = 'pending'")
            total_count = cursor.fetchone()[0]

            # Теперь получаем саму страницу
            offset = (page - 1) * page_size
            cursor.execute('''
                SELECT na.*, ns.name as source_name
                FROM news_articles na
                JOIN news_sources ns ON na.source_id = ns.id
                WHERE na.status = 'pending'
                ORDER BY na.created_at DESC
                LIMIT ? OFFSET ?
            ''', (page_size, offset))
            
            articles = [dict(row) for row in cursor.fetchall()]
            return articles, total_count
    
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
    
    # --- Методы для управления ключевыми словами ---
    
    def get_keywords(self) -> List[str]:
        """Получить все ключевые слова из базы данных."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT keyword FROM keywords ORDER BY keyword")
            return [row[0] for row in cursor.fetchall()]

    def add_keyword(self, keyword: str) -> bool:
        """Добавить новое ключевое слово. Возвращает True, если успешно."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO keywords (keyword) VALUES (?)", (keyword.lower(),))
                return True
        except sqlite3.IntegrityError:
            logger.warning(f"Ключевое слово '{keyword}' уже существует в базе.")
            return False
    
    def delete_keyword(self, keyword: str) -> bool:
        """Удалить ключевое слово. Возвращает True, если успешно."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM keywords WHERE keyword = ?", (keyword.lower(),))
            return cursor.rowcount > 0

    def delete_duplicate_articles(self) -> int:
        """
        Этот метод больше не нужен для постоянного вызова. 
        Очистка происходит один раз при старте в init_database.
        Возвращаем 0, чтобы не влиять на логику бота.
        """
        # logger.info("delete_duplicate_articles вызван, но очистка теперь происходит при старте.")
        return 0

    def delete_old_articles(self, days_old: int = 7) -> int:
        """
        Удаляет статьи старше определенного количества дней.
        По умолчанию удаляет статьи старше 7 дней.
        Возвращает количество удаленных статей.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Используем datetime('now', '-X days') для определения пороговой даты
            # Это надежный способ для работы с датами в SQLite
            cursor.execute(
                "DELETE FROM news_articles WHERE created_at < datetime('now', ?)",
                (f'-{days_old} days',)
            )
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            if deleted_count > 0:
                logger.info(f"Плановая очистка: удалено {deleted_count} статей старше {days_old} дней.")
            else:
                logger.info(f"Плановая очистка: не найдено статей старше {days_old} дней для удаления.")
                
            return deleted_count

    def delete_article(self, article_id: int) -> bool:
        """Удаляет статью из базы данных по ее ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM news_articles WHERE id = ?", (article_id,))
                conn.commit()
                logger.info(f"Статья с ID {article_id} удалена из базы данных.")
                return cursor.rowcount > 0
            except sqlite3.Error as e:
                logger.error(f"Ошибка при удалении статьи с ID {article_id}: {e}")
                conn.rollback()
                return False
            finally:
                cursor.close()

    def clear_all_articles(self) -> int:
        """
        Удаляет ВСЕ статьи из базы данных.
        Возвращает количество удаленных статей.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT COUNT(*) FROM news_articles")
                total_count = cursor.fetchone()[0]
                
                cursor.execute("DELETE FROM news_articles")
                conn.commit()
                
                logger.info(f"Полная очистка базы: удалено {total_count} статей.")
                return total_count
            except sqlite3.Error as e:
                logger.error(f"Ошибка при полной очистке базы: {e}")
                conn.rollback()
                return 0
            finally:
                cursor.close()
