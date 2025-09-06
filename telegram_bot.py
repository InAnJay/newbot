import logging
import asyncio
from typing import Dict, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import BadRequest
import requests
import os
from datetime import datetime
import json
from urllib.parse import urlparse, urlunparse

from config import TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, TARGET_CHANNEL_ID
from database import Database
from news_scraper import NewsScraper
from mistral_client import MistralClient
from openai_client import OpenAIClient

logger = logging.getLogger(__name__)

# Определяем состояния для диалога добавления источника
SOURCE_URL, SOURCE_NAME, SOURCE_TYPE = range(3)

class NewsBot:
    def __init__(self):
        self.db = Database()
        self.scraper = NewsScraper()
        self.mistral = MistralClient()
        self.openai = OpenAIClient()
        self.current_articles = {}  # Хранит текущие статьи для каждого пользователя
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user_id = update.effective_user.id
        
        if user_id != ADMIN_USER_ID:
            await update.message.reply_text("❌ У вас нет доступа к этому боту.")
            return
        
        keyboard = [
            [InlineKeyboardButton("📰 Просмотреть новости", callback_data="view_news")],
            [InlineKeyboardButton("⚙️ Управление источниками", callback_data="manage_sources")],
            [InlineKeyboardButton("🔄 Проверить источники", callback_data="check_sources")],
            [InlineKeyboardButton("📊 Статистика", callback_data="statistics")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🤖 Добро пожаловать в бота для управления новостями о маркетплейсах!\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        if user_id != ADMIN_USER_ID:
            await query.edit_message_text("❌ У вас нет доступа к этому боту.")
            return
        
        data = query.data
        
        if data == "view_news":
            await self.show_pending_news(query, context)
        elif data == "manage_sources":
            await self.manage_sources(query)
        elif data == "check_sources":
            await self.check_sources(context)
        elif data == "statistics":
            await self.show_statistics(query)
        elif data.startswith("article_"):
            await self.show_article_details(query, data, context)
        elif data.startswith("rewrite_"):
            await self.rewrite_article(query, data, context)
        elif data.startswith("new_image_"):
            await self.generate_new_image(query, data, context)
        elif data.startswith("publish_"):
            await self.publish_article(query, data, context)
        elif data.startswith("reject_"):
            await self.reject_article(query, data, context)
        elif data == "main_menu":
            await self.show_main_menu(query, context)
        elif data == "add_source":
            await self.show_add_source_form(update, context)
        elif data.startswith("delete_source_"):
            await self.delete_source(query, data)
        elif data == "noop":
            await query.answer() # Просто подтверждаем нажатие, ничего не делаем
    
    async def show_main_menu(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Показать главное меню (надежная версия)"""
        keyboard = [
            [InlineKeyboardButton("📰 Просмотреть новости", callback_data="view_news")],
            [InlineKeyboardButton("⚙️ Управление источниками", callback_data="manage_sources")],
            [InlineKeyboardButton("🔄 Проверить источники", callback_data="check_sources")],
            [InlineKeyboardButton("📊 Статистика", callback_data="statistics")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "🤖 Главное меню бота для управления новостями о маркетплейсах\n\nВыберите действие:"
        
        try:
            await query.edit_message_text(
                text,
                reply_markup=reply_markup
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                # Сообщение не изменилось, ничего не делаем
                pass
            elif "There is no text in the message to edit" in str(e):
                # Не можем отредактировать сообщение с фото, поэтому удаляем его и отправляем новое
                await query.delete_message()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=text,
                    reply_markup=reply_markup
                )
            else:
                # Пробрасываем другие, неизвестные ошибки
                raise
    
    async def show_pending_news(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Показывает список новостей на модерации (надежная версия)."""
        articles = self.db.get_pending_articles()
        
        keyboard = []
        if not articles:
            message_text = "✅ Все новости обработаны! Новых статей для модерации нет."
        else:
            message_text = "📰 **Новости на модерацию:**\n\nВыберите новость для просмотра."
            for article in articles:
                title = (article['original_title'] or 'Без заголовка')[:50]
                keyboard.append(
                    [InlineKeyboardButton(title, callback_data=f"article_{article['id']}")]
                )

        keyboard.append([InlineKeyboardButton("🔙 Назад в меню", callback_data="main_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            elif "There is no text in the message to edit" in str(e):
                # Не можем отредактировать сообщение с фото, поэтому удаляем его и отправляем новое
                await query.delete_message()
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                raise

    async def send_article_for_review(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, article_id: int):
        """Отправляет новое сообщение со статьей на проверку."""
        article = self.db.get_article_by_id(article_id)
        if not article:
            await context.bot.send_message(chat_id, "Не удалось найти статью.")
            return

        # Если статья еще не обработана, обрабатываем её
        if not article['rewritten_title']:
            processing_message = await context.bot.send_message(chat_id, "⏳ Обрабатываю статью (текст и изображение)...")
            
            rewritten = self.mistral.rewrite_news_article(
                article['original_title'], 
                article['original_content']
            )
            image_url = self.openai.generate_image(
                rewritten['title'],
                rewritten['content']
            )
            
            self.db.update_article_rewrite(
                article_id, rewritten['title'], rewritten['content'], rewritten['hashtags']
            )
            if image_url: # Теперь это локальный путь
                self.db.update_article_image(article_id, "", image_url) # Меняем местами URL и путь
            
            await processing_message.delete()
            article = self.db.get_article_by_id(article_id) # Получаем обновленные данные
        
        hashtags = json.loads(article['hashtags']) if article.get('hashtags') else []
        
        message = f"**{article['rewritten_title']}**\n\n"
        message += f"{article['rewritten_content']}\n\n"
        if hashtags:
            message += " ".join(hashtags) + "\n\n"
        message += f"🔗 Источник: {article['original_url']}"
        
        keyboard = [
            [
                InlineKeyboardButton("✏️ Переписать", callback_data=f"rewrite_{article_id}"),
                InlineKeyboardButton("🖼️ Новая картинка", callback_data=f"new_image_{article_id}")
            ],
            [
                InlineKeyboardButton("✅ Опубликовать", callback_data=f"publish_{article_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{article_id}")
            ],
            [InlineKeyboardButton("🔙 Назад к списку", callback_data="view_news")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        image_path = article.get('image_path')
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, 'rb') as photo_file:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo_file,
                        caption=message,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=reply_markup
                    )
            finally:
                # Удаляем файл после отправки
                if os.path.exists(image_path):
                    os.remove(image_path)
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )

    async def show_article_details(self, query, data, context: ContextTypes.DEFAULT_TYPE):
        """Показать детали статьи"""
        article_id = int(data.split("_")[1])
        article = self.db.get_article_by_id(article_id)
        
        if not article:
            await query.edit_message_text("❌ Статья не найдена.")
            return

        # Удаляем предыдущее сообщение (список новостей)
        await query.delete_message()

        # Отправляем статью как новое сообщение
        await self.send_article_for_review(context, query.message.chat_id, article_id)


    async def rewrite_article(self, query, data, context: ContextTypes.DEFAULT_TYPE):
        """Переписать статью (надежная версия)"""
        article_id = int(data.split("_")[1])
        article = self.db.get_article_by_id(article_id)
        
        if not article:
            await query.answer("❌ Статья не найдена.", show_alert=True)
            return
        
        await query.delete_message()
        processing_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⏳ Переписываю статью с использованием улучшенного промпта..."
        )
        
        # Переписываем статью
        rewritten = self.mistral.rewrite_news_article(
            article['original_title'], 
            article['original_content']
        )
        
        # Сохраняем в базу
        self.db.update_article_rewrite(
            article_id, 
            rewritten['title'], 
            rewritten['content'],
            rewritten['hashtags']
        )

        if image_url: # Теперь это локальный путь
            self.db.update_article_image(article_id, "", image_url) # Меняем местами URL и путь
        
        # Обновляем данные статьи
        article = self.db.get_article_by_id(article_id) # Получаем обновленные данные
        
        # Удаляем временное сообщение "Переписываю..."
        await processing_message.delete()

        # Показываем обновленную статью как новое сообщение
        await self.send_article_for_review(context, query.message.chat_id, article_id)
    
    async def generate_new_image(self, query, data, context: ContextTypes.DEFAULT_TYPE):
        """Сгенерировать новое изображение (надежная версия)"""
        article_id = int(data.split("_")[2])
        article = self.db.get_article_by_id(article_id)
        
        if not article:
            await query.answer("❌ Статья не найдена.", show_alert=True)
            return
        
        # Удаляем старое сообщение и отправляем временное
        await query.delete_message()
        processing_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⏳ Генерирую новое изображение..."
        )
        
        # Генерируем новое изображение
        image_path = self.openai.generate_image(
            article['rewritten_title'] or article['original_title'], 
            article['rewritten_content'] or article['original_content']
        )
        
        if image_path:
            self.db.update_article_image(article_id, "", image_path) # Сохраняем локальный путь
        else:
            await processing_message.edit_text("❌ Не удалось сгенерировать изображение. Показываю статью со старым изображением.")
            await asyncio.sleep(2)

        # Удаляем временное сообщение
        await processing_message.delete()

        # Показываем обновленную статью как новое сообщение
        await self.send_article_for_review(context, query.message.chat_id, article_id)

    async def publish_article(self, query, data, context: ContextTypes.DEFAULT_TYPE):
        """Публикует статью в целевой канал."""
        article_id = int(data.split("_")[1])
        
        if not TARGET_CHANNEL_ID:
            await query.answer("❌ ID канала для публикации (TARGET_CHANNEL_ID) не настроен!", show_alert=True)
            return

        article = self.db.get_article_by_id(article_id)
        if not article:
            await query.answer("❌ Не могу найти статью для публикации.", show_alert=True)
            return

        # Формируем финальный пост
        hashtags = json.loads(article['hashtags']) if article.get('hashtags') else []
        
        message = f"**{article['rewritten_title']}**\n\n"
        message += f"{article['rewritten_content']}\n\n"
        if hashtags:
            message += " ".join(hashtags) + "\n\n"
        message += f"🔗 Источник: {article['original_url']}"

        try:
            await query.answer("⏳ Публикую...")
            image_path = article.get('image_path')
            
            if image_path and os.path.exists(image_path):
                with open(image_path, 'rb') as photo_file:
                    await context.bot.send_photo(
                        chat_id=TARGET_CHANNEL_ID,
                        photo=photo_file,
                        caption=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
            else:
                # Если изображения нет, отправляем только текст
                await context.bot.send_message(
                    chat_id=TARGET_CHANNEL_ID,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
            
            # Обновляем статус статьи в БД
            self.db.update_article_status(article_id, 'published')
            await query.delete_message() # Удаляем старое сообщение
            
            # Информируем админа и показываем следующую статью
            await context.bot.send_message(query.message.chat_id, "✅ Статья успешно опубликована в канале!")

        except Exception as e:
            logger.error(f"Ошибка при публикации статьи {article_id} в канал {TARGET_CHANNEL_ID}: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ Произошла ошибка при публикации: {e}"
            )
            return # Прерываем выполнение в случае ошибки

        # Показываем следующую статью или возвращаемся в меню
        articles = self.db.get_pending_articles()
        if articles:
            await self.send_article_for_review(context, query.message.chat_id, articles[0]['id'])
        else:
            await context.bot.send_message(
                query.message.chat_id,
                "✅ Все новости обработаны! Новых статей для модерации нет.",
                reply_markup=self.get_main_menu_keyboard()
            )

    async def reject_article(self, query, data, context: ContextTypes.DEFAULT_TYPE):
        """Отклонить статью"""
        article_id = int(data.split("_")[1])
        
        self.db.update_article_status(article_id, 'rejected')
        
        await query.delete_message()
        await context.bot.send_message(query.message.chat_id, "❌ Статья отклонена.")
        
        # Показываем следующую статью
        articles = self.db.get_pending_articles()
        if articles:
            await self.send_article_for_review(context, query.message.chat_id, articles[0]['id'])
        else:
            await context.bot.send_message(
                query.message.chat_id,
                "✅ Все новости обработаны!",
                reply_markup=self.get_main_menu_keyboard()
            )

    async def manage_sources(self, query):
        """Показать управление источниками"""
        sources = self.db.get_news_sources(active_only=False)
        
        keyboard = []
        for source in sources:
            status = "✅" if source.get('is_active', 1) else "❌"
            keyboard.append([
                InlineKeyboardButton(
                    f"{status} {source['name']}", 
                    callback_data=f"noop" # Placeholder, toggle not implemented
                ),
                InlineKeyboardButton(
                    "❌ Удалить", 
                    callback_data=f"delete_source_{source['id']}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("➕ Добавить источник", callback_data="add_source")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = "⚙️ **Управление источниками новостей**\n\n"
        if not sources:
            message += "Пока нет добавленных источников."
        else:
            message += "Вы можете добавить новый источник или удалить существующий."
        
        try:
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                raise

    async def delete_source(self, query, data):
        """Удалить источник новостей"""
        source_id = int(data.split("_")[2])
        
        source = self.db.get_source_by_id(source_id)
        if not source:
            await query.answer("❌ Источник не найден.", show_alert=True)
            return

        try:
            self.db.delete_news_source(source_id)
            await query.answer(f"✅ Источник '{source['name']}' удален.")
        except Exception as e:
            logger.error(f"Ошибка при удалении источника {source_id}: {e}")
            await query.answer("❌ Произошла ошибка при удалении.", show_alert=True)
        
        # Обновляем список источников
        await self.manage_sources(query)

    def normalize_url(self, url: str) -> str:
        """Нормализует URL, убирая параметры, фрагменты и конечный слэш."""
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            path = parsed.path.rstrip('/')
            normalized = urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))
            return normalized
        except Exception as e:
            logger.warning(f"Не удалось нормализовать URL '{url}': {e}")
            return url

    async def check_sources(self, context: ContextTypes.DEFAULT_TYPE):
        """Проверяет все источники на наличие новых статей."""
        await context.bot.send_message(ADMIN_USER_ID, "⏳ Проверяю источники на новые новости...")
        
        sources = self.db.get_news_sources(active_only=True)
        new_articles_count = 0
        
        for source in sources:
            try:
                articles = self.scraper.scrape_source(source['source_type'], source['url'])
                
                for article in articles:
                    # Нормализуем URL перед проверкой и добавлением
                    normalized_url = self.normalize_url(article['url'])
                    if not normalized_url:
                        continue
                        
                    # Проверяем, не существует ли уже такая статья по URL
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
                
                # Обновляем время последней проверки
                self.db.update_source_last_check(source['id'])
                
            except Exception as e:
                logger.error(f"Ошибка при проверке источника {source['name']}: {e}")
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            ADMIN_USER_ID,
            f"✅ Проверка завершена!\n\n"
            f"Найдено новых статей: {new_articles_count}\n"
            f"Проверено источников: {len(sources)}",
            reply_markup=reply_markup
        )
    
    async def show_statistics(self, query):
        """Показать статистику"""
        # Здесь можно добавить более детальную статистику
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = ("📊 **Статистика бота**\n\n"
                "Функция в разработке...")
        
        try:
            await query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                raise
    
    def get_main_menu_keyboard(self):
        """Возвращает клавиатуру главного меню."""
        keyboard = [
            [InlineKeyboardButton("📰 Просмотреть новости", callback_data="view_news")],
            [InlineKeyboardButton("⚙️ Управление источниками", callback_data="manage_sources")],
            [InlineKeyboardButton("🔄 Проверить источники", callback_data="check_sources")],
            [InlineKeyboardButton("📊 Статистика", callback_data="statistics")]
        ]
        return InlineKeyboardMarkup(keyboard)

    # --- Начало блока ConversationHandler для добавления источника ---

    async def show_add_source_form(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начало диалога добавления источника."""
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            "➕ **Добавление нового источника**\n\n"
            "Пожалуйста, отправьте мне ссылку (URL) на новостной источник.\n\n"
            "Чтобы отменить, отправьте команду /cancel."
        )
        return SOURCE_URL

    async def receive_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получение URL и запрос названия."""
        url = update.message.text
        if not url.startswith('http'):
            await update.message.reply_text(
                "Это не похоже на ссылку. Пожалуйста, отправьте корректный URL, начинающийся с http или https."
            )
            return SOURCE_URL
        
        context.user_data['source_url'] = url
        await update.message.reply_text(
            "Отлично! Теперь придумайте короткое и понятное название для этого источника (например, 'Новости Shoppers')."
        )
        return SOURCE_NAME

    async def receive_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получение названия и запрос типа."""
        context.user_data['source_name'] = update.message.text
        
        keyboard = [
            [
                InlineKeyboardButton("RSS", callback_data="rss"),
                InlineKeyboardButton("Веб-сайт", callback_data="website"),
                InlineKeyboardButton("Telegram", callback_data="telegram"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("Спасибо! Остался последний шаг. Выберите тип источника:", reply_markup=reply_markup)
        return SOURCE_TYPE

    async def receive_type(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получение типа, сохранение и завершение диалога."""
        query = update.callback_query
        await query.answer()
        
        source_type = query.data
        name = context.user_data.get('source_name')
        url = context.user_data.get('source_url')
        
        try:
            source_id = self.db.add_news_source(name, url, source_type)
            message = (
                f"✅ Источник успешно добавлен!\n\n"
                f"**Название:** {name}\n"
                f"**Тип:** {source_type}\n"
                f"**URL:** {url}"
            )
            await query.edit_message_text(
                message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=self.get_back_to_menu_keyboard()
            )
        except Exception as e:
            await query.edit_message_text(
                f"❌ Произошла ошибка при добавлении источника: {e}",
                reply_markup=self.get_back_to_menu_keyboard()
            )
        
        context.user_data.clear()
        return ConversationHandler.END
        
    async def cancel_add_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отмена процесса добавления источника."""
        await update.message.reply_text(
            "Действие отменено. Вы вернулись в главное меню.",
            reply_markup=self.get_main_menu_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    # --- Конец блока ConversationHandler ---
    
    def get_back_to_menu_keyboard(self):
        """Возвращает клавиатуру с одной кнопкой 'Назад в меню'."""
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="main_menu")]]
        return InlineKeyboardMarkup(keyboard)

    async def handle_unknown_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик неизвестных текстовых сообщений."""
        user_id = update.effective_user.id
        if user_id != ADMIN_USER_ID:
            return
        
        await update.message.reply_text(
            "Неизвестная команда. Пожалуйста, используйте кнопки в меню для управления ботом.",
            reply_markup=self.get_main_menu_keyboard()
        )
    
    def run(self):
        """Запуск бота"""
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен в переменных окружения")
        
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Создаем ConversationHandler для диалога добавления источника
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.show_add_source_form, pattern='^add_source$')],
            states={
                SOURCE_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_url)],
                SOURCE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_name)],
                SOURCE_TYPE: [CallbackQueryHandler(self.receive_type, pattern='^(rss|website|telegram)$')],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_add_source)],
        )
        
        # Добавляем обработчики. ConversationHandler должен быть первым.
        application.add_handler(conv_handler)
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CallbackQueryHandler(self.button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_unknown_message))
        
        # Запускаем бота
        application.run_polling()
