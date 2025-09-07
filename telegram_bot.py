import logging
import asyncio
from typing import Dict, List
from functools import wraps
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
from scheduler import NewsScheduler
from mistral_client import MistralClient
from openai_client import OpenAIClient

logger = logging.getLogger(__name__)

# --- Декоратор для проверки прав администратора ---
def admin_only(func):
    """Декоратор, который проверяет, является ли пользователь администратором."""
    @wraps(func)
    async def wrapped(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_USER_ID:
            message = "❌ У вас нет доступа к этому боту."
            if update.callback_query:
                await update.callback_query.answer(message, show_alert=True)
            elif update.message:
                await update.message.reply_text(message)
            return
        return await func(self, update, context, *args, **kwargs)
    return wrapped

# Определяем состояния для диалога добавления источника
SOURCE_URL, SOURCE_NAME, SOURCE_TYPE = range(3)
# Определяем состояния для диалога управления ключевыми словами
KEYWORD_MANAGE, KEYWORD_ADD, KEYWORD_DELETE = range(3, 6)
# Определяем состояния для диалога редактирования источника
EDIT_SOURCE_NAME, EDIT_SOURCE_URL = range(6, 8)


class NewsBot:
    def __init__(self, db: Database, scheduler: NewsScheduler, mistral: MistralClient, openai: OpenAIClient):
        self.db = db
        self.scheduler = scheduler
        self.mistral = mistral
        self.openai = openai
        self.current_articles = {}  # Хранит текущие статьи для каждого пользователя
        
    @admin_only
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        await update.message.reply_text(
            "🤖 Добро пожаловать в бота для управления новостями о маркетплейсах!\n\n"
            "Выберите действие:",
            reply_markup=self.get_main_menu_keyboard()
        )
    
    @admin_only
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "view_news":
            await self.show_pending_news(query, context)
        elif data == "manage_sources":
            await self.manage_sources(query)
        elif data == "check_sources":
            await self.check_sources(query, context)
        elif data == "manage_keywords":
            await self.manage_keywords_menu(update, context)
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
        elif data.startswith('view_news_page_'):
            try:
                page = int(data.replace('view_news_page_', ''))
                # Добавим защиту, чтобы страница не могла быть меньше 1
                if page < 1:
                    page = 1
                await self.show_pending_news(query, context, page=page)
            except (ValueError, TypeError):
                await query.answer("❌ Ошибка: неверный номер страницы.")
        elif data.startswith('delete_article_'):
            await self.delete_article_callback(query, context, data)
        elif data == "clear_database":
            await self.show_clear_database_confirmation(query, context)
        elif data == "confirm_clear_database":
            await self.clear_database(query, context)
        elif data == "cancel_clear_database":
            await self.show_main_menu(query, context)
        elif data == "add_source":
            await self.show_add_source_form(update, context)
        elif data.startswith("view_source_"):
            await self.view_source_details(query, data)
        elif data.startswith("delete_source_"):
            await self.delete_source(query, data)
        elif data.startswith('edit_source_'):
            await self.start_edit_source(query, context)
        else:
            await query.answer("Неизвестная команда.")

    async def delete_article_callback(self, query: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
        """Обрабатывает нажатие кнопки удаления статьи."""
        try:
            article_id = int(data.replace('delete_article_', ''))
            
            # Удаляем статью из БД
            success = self.db.delete_article(article_id)

            if success:
                await query.answer("✅ Новость удалена")
                # Обновляем список новостей, чтобы удаленная новость исчезла
                # Получаем текущую страницу из callback_data кнопки "Назад" в сообщении
                current_page = 1
                if query.message and query.message.reply_markup:
                    for row in query.message.reply_markup.inline_keyboard:
                        for button in row:
                            if button.callback_data.startswith('view_news_page_'):
                                try:
                                    current_page = int(button.callback_data.replace('view_news_page_', ''))
                                    break
                                except ValueError:
                                    pass
                        if current_page != 1:
                            break
                
                await self.show_pending_news(query, context, page=current_page)
            else:
                await query.answer("❌ Ошибка при удалении новости")
        except (IndexError, ValueError):
            await query.answer("❌ Ошибка: неверный ID статьи для удаления.")
        except Exception as e:
            logger.error(f"Ошибка в delete_article_callback: {e}")
            await query.answer("❌ Произошла внутренняя ошибка.")

    async def show_clear_database_confirmation(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Показывает подтверждение для очистки базы данных."""
        text = (
            "⚠️ **ВНИМАНИЕ!**\n\n"
            "Вы собираетесь удалить ВСЕ статьи из базы данных.\n"
            "Это действие нельзя отменить!\n\n"
            "Продолжить?"
        )
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, удалить всё", callback_data="confirm_clear_database"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_clear_database")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                await query.answer()
            else:
                logger.error(f"Ошибка при редактировании сообщения: {e}")

    async def clear_database(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Выполняет полную очистку базы данных."""
        try:
            await query.answer("⏳ Очищаю базу данных...")
            
            # Выполняем очистку в отдельном потоке, чтобы не блокировать бота
            loop = asyncio.get_event_loop()
            deleted_count = await loop.run_in_executor(None, self.db.clear_all_articles)
            
            text = f"✅ **База данных очищена!**\n\nУдалено статей: **{deleted_count}**"
            keyboard = [[InlineKeyboardButton("🔙 В главное меню", callback_data="main_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Ошибка при очистке базы данных: {e}")
            await query.edit_message_text(
                text=f"❌ Произошла ошибка при очистке базы данных: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 В главное меню", callback_data="main_menu")]])
            )
            
    async def show_main_menu(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Показать главное меню (надежная версия)"""
        text = "🤖 Главное меню бота для управления новостями о маркетплейсах\n\nВыберите действие:"
        reply_markup = self.get_main_menu_keyboard()
        
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

    async def show_pending_news(self, query, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
        """Показывает список новостей на модерации с пагинацией."""
        
        # Данные теперь очищаются один раз при старте, убираем постоянную очистку.
        # Это предотвратит "прыжки" в количестве страниц.

        # 1. Получаем актуальные данные
        page_size = 15
        articles, total_articles = self.db.get_pending_articles_paginated(page=page, page_size=page_size)
        
        total_pages = (total_articles + page_size - 1) // page_size
        if total_pages == 0: total_pages = 1

        # 2. Проверяем, не "исчезла" ли наша страница (например, из-за удаления статей вручную)
        if page > total_pages:
            await query.answer(f"Список новостей обновился. Перенаправляю на последнюю страницу ({total_pages}).", show_alert=True)
            # Рекурсивно вызываем себя с правильной, последней страницей
            await self.show_pending_news(query, context, page=total_pages)
            return

        # 3. Формируем и отображаем сообщение
        if not articles and page == 1:
            text = "✅ Все новости обработаны! Новых статей для модерации нет."
            keyboard = [[InlineKeyboardButton("🔙 В главное меню", callback_data="main_menu")]]
        else:
            text = f"📰 Новости на модерации ({total_articles} шт.)\n\nСтраница {page}/{total_pages}"
            
            keyboard = []
            for article in articles:
                # Используем 'original_title', так как это ключ из БД для списка
                title_text = article.get('original_title') or 'Без заголовка'
                # Ограничиваем заголовок до 50 символов, чтобы оставить место для кнопки удаления
                short_title = title_text if len(title_text) < 50 else title_text[:47] + "..."
                
                # Создаем строку только с заголовком (занимает всю ширину)
                keyboard.append([InlineKeyboardButton(short_title, callback_data=f"view_article_{article['id']}")])

            pagination_row = []
            if page > 1:
                pagination_row.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"view_news_page_{page - 1}"))
            if page < total_pages:
                pagination_row.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"view_news_page_{page + 1}"))
            
            if pagination_row:
                keyboard.append(pagination_row)
            
            keyboard.append([InlineKeyboardButton("🔙 В главное меню", callback_data="main_menu")])

        try:
            await context.bot.edit_message_text(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                await query.answer()
            else:
                logger.error(f"Не удалось отредактировать сообщение в show_pending_news: {e}")
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в show_pending_news: {e}")

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
            [
                InlineKeyboardButton("🗑️ Удалить статью", callback_data=f"delete_article_{article_id}")
            ],
            [InlineKeyboardButton("🔙 Назад к списку", callback_data="view_news_page_1")]
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
        
        # Удаляем временное сообщение "Переписываю..."
        await processing_message.delete()

        # Показываем обновленную статью как новое сообщение
        await self.send_article_for_review(context, query.message.chat_id, article_id)
    
    async def generate_new_image(self, query, data, context: ContextTypes.DEFAULT_TYPE):
        """Сгенерировать новое изображение (надежная версия)"""
        article_id = int(data.split("_")[1])
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
                # Удаляем файл после успешной публикации
                try:
                    os.remove(image_path)
                except OSError as e:
                    logger.warning(f"Не удалось удалить файл изображения {image_path}: {e}")
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
            # Теперь вся строка - это кнопка для просмотра деталей
            keyboard.append([
                InlineKeyboardButton(
                    f"{status} {source['name']}", 
                    callback_data=f"view_source_{source['id']}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("➕ Добавить источник", callback_data="add_source")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = "⚙️ **Управление источниками новостей**\n\nНажмите на источник, чтобы просмотреть детали и изменить его."
        if not sources:
            message = "Пока нет добавленных источников. Нажмите 'Добавить', чтобы начать."
        
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

    async def view_source_details(self, query, data):
        """Показать детальную информацию об источнике и кнопки для редактирования."""
        try:
            source_id = int(data.replace('view_source_', ''))
        except (ValueError, IndexError):
            await query.answer("❌ Неверный ID источника.", show_alert=True)
            return

        source = self.db.get_source_by_id(source_id)

        if not source:
            await query.answer("❌ Источник не найден.", show_alert=True)
            return

        text = (
            f"**Источник:** `{source['name']}`\n"
            f"**Тип:** `{source['source_type']}`\n"
            f"**URL:** `{source['url']}`"
        )

        keyboard = [
            [
                InlineKeyboardButton("✏️ Изменить название", callback_data=f"edit_name_{source_id}"),
                InlineKeyboardButton("✏️ Изменить URL", callback_data=f"edit_url_{source_id}")
            ],
            [
                 InlineKeyboardButton("❌ Удалить источник", callback_data=f"delete_source_{source_id}")
            ],
            [
                InlineKeyboardButton("🔙 Назад к источникам", callback_data="manage_sources")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

    async def delete_source(self, query, data):
        """Удалить источник новостей"""
        try:
            source_id = int(data.replace('delete_source_', ''))
        except (ValueError, IndexError):
            await query.answer("❌ Неверный формат ID источника.", show_alert=True)
            return
        
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

    async def check_sources(self, query, context: ContextTypes.DEFAULT_TYPE):
        """Запускает принудительную проверку источников и сообщает результат."""
        await query.answer("⏳ Запускаю проверку источников... Это может занять некоторое время.")
        
        # Редактируем сообщение, чтобы показать, что идет работа
        try:
            await query.edit_message_text(
                text="⏳ Выполняется проверка источников... Пожалуйста, подождите.",
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass # Ничего страшного, если сообщение уже такое
            else:
                raise

        # Запускаем тяжелую задачу в отдельном потоке
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, self.scheduler.force_check_sources
        )

        # Сообщаем результат и снова показываем меню
        total = results.get('total', 0)
        by_source = results.get('by_source', {})

        text = f"✅ **Проверка завершена!**\n\nНайдено новых статей: **{total}**\n\n"

        if total > 0:
            text += "В том числе:\n"
            for source_name, count in by_source.items():
                if count > 0:
                    text += f"- `{source_name}`: **{count}**\n"
        
        text += "\nНовые статьи (если они есть) теперь доступны для модерации."
        
        await query.edit_message_text(
            text=text,
            reply_markup=self.get_main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
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
            [InlineKeyboardButton("🔑 Управление словами", callback_data="manage_keywords")],
            [InlineKeyboardButton("🔄 Проверить источники", callback_data="check_sources")],
            [InlineKeyboardButton("📊 Статистика", callback_data="statistics")],
            [InlineKeyboardButton("🗑️ Очистить базу данных", callback_data="clear_database")]
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

        if not name or not url:
            await query.edit_message_text(
                "❌ Произошла ошибка: не удалось найти данные об источнике. Попробуйте снова.",
                reply_markup=self.get_back_to_menu_keyboard()
            )
            context.user_data.clear()
            return ConversationHandler.END

        normalized_url = self.normalize_url(url)
        
        try:
            source_id = self.db.add_news_source(name, normalized_url, source_type)
            message = (
                f"✅ Источник успешно добавлен!\n\n"
                f"**Название:** {name}\n"
                f"**Тип:** {source_type}\n"
                f"**URL:** {normalized_url}"
            )
            await query.edit_message_text(
                message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
                reply_markup=self.get_back_to_menu_keyboard()
            )
        except ValueError as e: # Ловим конкретную ошибку от слоя базы данных
            await query.edit_message_text(
                f"❌ Ошибка: {e}",
                reply_markup=self.get_back_to_menu_keyboard()
            )
        except Exception as e: # Общая ошибка на всякий случай
            logger.error(f"Неожиданная ошибка при добавлении источника {name} ({normalized_url}): {e}")
            await query.edit_message_text(
                f"❌ Произошла непредвиденная ошибка при добавлении источника.",
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

    # --- Начало блока ConversationHandler для редактирования источника ---

    async def start_edit_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начало диалога редактирования. Запрашивает новое значение."""
        query = update.callback_query
        await query.answer()

        try:
            action, source_id_str = query.data.rsplit("_", 1)
            source_id = int(source_id_str)
        except (ValueError, IndexError):
            await query.edit_message_text("❌ Ошибка: не удалось распознать команду. Попробуйте снова.")
            return ConversationHandler.END

        context.user_data['edit_source_id'] = source_id

        if action == 'edit_name':
            context.user_data['edit_field'] = 'name'
            await query.edit_message_text("Пожалуйста, введите новое название для источника.\n\nДля отмены введите /cancel")
            return EDIT_SOURCE_NAME
        elif action == 'edit_url':
            context.user_data['edit_field'] = 'url'
            await query.edit_message_text("Пожалуйста, введите новый URL для источника.\n\nДля отмены введите /cancel")
            return EDIT_SOURCE_URL

    async def receive_new_source_value(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Получает новое значение, обновляет в БД и завершает диалог."""
        new_value = update.message.text.strip()
        source_id = context.user_data['edit_source_id']
        field_to_edit = context.user_data['edit_field']
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К списку источников", callback_data="manage_sources")]])

        try:
            if field_to_edit == 'name':
                self.db.update_source_details(source_id, name=new_value)
            elif field_to_edit == 'url':
                if not new_value.startswith('http'):
                    await update.message.reply_text("Это не похоже на ссылку. URL должен начинаться с http или https. Попробуйте снова.")
                    return EDIT_SOURCE_URL
                normalized_url = self.normalize_url(new_value)
                self.db.update_source_details(source_id, url=normalized_url)
            
            await update.message.reply_text("✅ Данные источника успешно обновлены!", reply_markup=keyboard)

        except ValueError as e:
            await update.message.reply_text(f"❌ Ошибка: {e}\n\nПопробуйте ввести другое значение.")
            return EDIT_SOURCE_URL if field_to_edit == 'url' else EDIT_SOURCE_NAME
        except Exception as e:
            logger.error(f"Ошибка при обновлении источника {source_id}: {e}")
            await update.message.reply_text("❌ Произошла непредвиденная ошибка.", reply_markup=keyboard)

        context.user_data.clear()
        return ConversationHandler.END

    async def cancel_edit_source(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отмена процесса редактирования."""
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 К списку источников", callback_data="manage_sources")]])
        await update.message.reply_text("Редактирование отменено.", reply_markup=keyboard)
        context.user_data.clear()
        return ConversationHandler.END

    # --- Конец блока ConversationHandler ---
    
    # --- Начало блока ConversationHandler для управления ключевыми словами ---

    async def manage_keywords_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Показывает меню управления ключевыми словами."""
        query = update.callback_query
        await query.answer()

        keywords = self.db.get_keywords()
        text = "🔑 **Управление ключевыми словами**\n\n"
        if keywords:
            text += "Текущие слова:\n`" + "`, `".join(keywords) + "`\n\n"
        else:
            text += "Ключевые слова пока не добавлены.\n\n"
        text += "Выберите действие:"

        keyboard = [
            [InlineKeyboardButton("➕ Добавить слово", callback_data="keyword_add")],
            [InlineKeyboardButton("➖ Удалить слово", callback_data="keyword_delete")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        return KEYWORD_MANAGE

    async def ask_for_keyword_to_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Запрашивает у пользователя слово для добавления."""
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("Введите ключевое слово для добавления. Для отмены введите /cancel.")
        return KEYWORD_ADD

    async def add_keyword(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Добавляет ключевое слово в базу."""
        keyword = update.message.text.strip().lower()
        if self.db.add_keyword(keyword):
            await update.message.reply_text(f"✅ Слово '{keyword}' успешно добавлено.")
        else:
            await update.message.reply_text(f"⚠️ Слово '{keyword}' уже существует.")
        
        # Возвращаемся в меню управления
        await self.manage_keywords_menu(update, context)
        return ConversationHandler.END

    async def ask_for_keyword_to_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Запрашивает у пользователя слово для удаления."""
        query = update.callback_query
        await query.answer()
        
        keywords = self.db.get_keywords()
        if not keywords:
            await query.edit_message_text("Нечего удалять. Список ключевых слов пуст.", reply_markup=self.get_back_to_menu_keyboard())
            return ConversationHandler.END

        keyboard = [[InlineKeyboardButton(kw, callback_data=f"delkw_{kw}")] for kw in keywords]
        keyboard.append([InlineKeyboardButton("🔙 Отмена", callback_data="keyword_manage")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text("Выберите ключевое слово для удаления:", reply_markup=reply_markup)
        return KEYWORD_DELETE

    async def delete_keyword(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Удаляет выбранное ключевое слово."""
        query = update.callback_query
        await query.answer()
        
        keyword_to_delete = query.data.split("_")[1]
        
        if self.db.delete_keyword(keyword_to_delete):
            await query.answer(f"✅ Слово '{keyword_to_delete}' удалено.")
        else:
            await query.answer(f"❌ Не удалось удалить слово '{keyword_to_delete}'.", show_alert=True)
            
        # Обновляем меню
        await self.manage_keywords_menu(update, context)
        return ConversationHandler.END

    async def cancel_keyword_manage(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отмена процесса управления ключевыми словами."""
        await update.message.reply_text(
            "Действие отменено. Вы вернулись в главное меню.",
            reply_markup=self.get_main_menu_keyboard()
        )
        context.user_data.clear()
        return ConversationHandler.END

    # --- Конец блока ConversationHandler для ключевых слов ---
    
    def get_back_to_menu_keyboard(self):
        """Возвращает клавиатуру с одной кнопкой 'Назад в меню'."""
        keyboard = [[InlineKeyboardButton("🔙 Назад в меню", callback_data="main_menu")]]
        return InlineKeyboardMarkup(keyboard)

    @admin_only
    async def handle_unknown_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик неизвестных текстовых сообщений."""
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
        add_source_conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.show_add_source_form, pattern='^add_source$')],
            states={
                SOURCE_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_url)],
                SOURCE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_name)],
                SOURCE_TYPE: [CallbackQueryHandler(self.receive_type, pattern='^(rss|website|telegram)$')],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_add_source)],
        )

        # Создаем ConversationHandler для редактирования источника
        edit_source_conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self.start_edit_source, pattern='^edit_name_'),
                CallbackQueryHandler(self.start_edit_source, pattern='^edit_url_')
            ],
            states={
                EDIT_SOURCE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_new_source_value)],
                EDIT_SOURCE_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_new_source_value)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_edit_source)],
        )

        # Создаем ConversationHandler для управления ключевыми словами
        manage_keywords_conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.manage_keywords_menu, pattern='^manage_keywords$')],
            states={
                KEYWORD_MANAGE: [
                    CallbackQueryHandler(self.ask_for_keyword_to_add, pattern='^keyword_add$'),
                    CallbackQueryHandler(self.ask_for_keyword_to_delete, pattern='^keyword_delete$'),
                    CallbackQueryHandler(self.show_main_menu_from_update, pattern='^main_menu$')
                ],
                KEYWORD_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.add_keyword)],
                KEYWORD_DELETE: [CallbackQueryHandler(self.delete_keyword, pattern='^delkw_')],
            },
            fallbacks=[CommandHandler('cancel', self.cancel_keyword_manage)],
            map_to_parent={
                # Возврат в главное меню
                ConversationHandler.END: ConversationHandler.END
            }
        )
        
        # Добавляем обработчики. ConversationHandler должен быть первым.
        application.add_handler(add_source_conv_handler)
        application.add_handler(edit_source_conv_handler)
        application.add_handler(manage_keywords_conv_handler)
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CallbackQueryHandler(self.button_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_unknown_message))
        
        # Запускаем бота
        application.run_polling()

    async def show_main_menu_from_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_main_menu(update.callback_query, context)
        return ConversationHandler.END
