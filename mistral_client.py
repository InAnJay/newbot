import json
import logging
from typing import Dict, List

from bs4 import BeautifulSoup
from mistralai.client import MistralClient as MistralAIClient
from mistralai.models.chat_completion import ChatMessage

from config import MISTRAL_API_KEY

logger = logging.getLogger(__name__)


class MistralClient:
    def __init__(self):
        if not MISTRAL_API_KEY:
            raise ValueError("MISTRAL_API_KEY не установлен в переменных окружения")

        self.client = MistralAIClient(api_key=MISTRAL_API_KEY)

    def rewrite_news_article(self, title: str, content: str) -> Dict[str, str]:
        """Переписать новостную статью в удобном формате с помощью Mistral."""
        try:
            prompt = f"""
Перепиши следующую новость о маркетплейсах в удобном и привлекательном формате для публикации в Telegram канале:

ЗАГОЛОВOК: {title}
СОДЕРЖАНИЕ: {content}

ТРЕБОВАНИЯ:
1. Создай новый заголовок (до 100 символов), который будет привлекательным и информативным.
2. Перепиши содержание в 3-6 предложений, сделав его более читаемым и интересным, но при этом сохранив все ключевые данные из оригинала.
3. Сохрани и ОБЯЗАТЕЛЬНО включи в текст ключевые факты, списки (например, ТОП товаров), имена или цифры из оригинального содержания. Текст должен быть не просто пересказом, а содержать конкретную полезную информацию.
4. Используй простой и понятный язык.
5. Добавь несколько релевантных хэштегов (3-5 штук).

ФОРМАТ ОТВЕТА (строго в виде одного JSON объекта, без дополнительных пояснений):
{{
    "title": "новый заголовок",
    "content": "переписанное содержание",
    "hashtags": ["#хэштег1", "#хэштег2", "#хэштег3"]
}}
"""
            messages = [
                ChatMessage(role="system", content="Ты эксперт по переписыванию новостей о маркетплейсах и e-commerce. Твоя задача - создавать привлекательный и информативный контент для социальных сетей. Всегда отвечай только в формате JSON."),
                ChatMessage(role="user", content=prompt)
            ]

            response = self.client.chat(
                model="mistral-small-latest",
                messages=messages,
                response_format={"type": "json_object"}
            )

            result_text = response.choices[0].message.content
            result_json = json.loads(result_text)
            
            return {
                'title': result_json.get('title', title),
                'content': result_json.get('content', content),
                'hashtags': result_json.get('hashtags', [])
            }

        except Exception as e:
            logger.error(f"Ошибка при переписывании статьи с помощью Mistral: {e}")
            return {'title': title, 'content': content, 'hashtags': []}

    def find_articles_on_page(self, page_html: str, base_url: str) -> List[Dict]:
        """Использует Mistral для поиска новостных статей на HTML-странице."""
        try:
            soup = BeautifulSoup(page_html, 'html.parser')
            for tag in soup(['script', 'style', 'svg', 'nav', 'footer', 'header', 'form']):
                tag.decompose()
            
            body_text = soup.get_text(separator='\n', strip=True)
            
            max_chars = 15000
            if len(body_text) > max_chars:
                body_text = body_text[:max_chars]

            prompt = f"""
Проанализируй следующий текстовый контент, извлеченный из HTML-страницы. Твоя задача - найти все новостные статьи или анонсы.

Для каждой найденной статьи извлеки:
1. `title` (заголовок)
2. `url` (полная ссылка на статью, если есть относительная - дополни ее базовым URL: {base_url})
3. `summary` (краткое описание или первый абзац)

Игнорируй рекламные блоки, навигационные меню и другой нерелевантный контент.
Верни результат в виде JSON-объекта, где ключ "articles" содержит массив найденных статей. Если статей не найдено, массив должен быть пустым [].

Пример формата:
{{
  "articles": [
    {{
      "title": "Пример заголовка новости",
      "url": "https://example.com/news/1",
      "summary": "Краткое описание новости..."
    }}
  ]
}}

Вот текстовое содержимое для анализа:
{body_text}
"""
            messages = [
                ChatMessage(role="system", content="Ты - AI-ассистент, который преобразует текст с веб-страниц в структурированные JSON-данные, находя новостные статьи. Всегда отвечай только в формате JSON."),
                ChatMessage(role="user", content=prompt)
            ]

            response = self.client.chat(
                model="mistral-large-latest",
                messages=messages,
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            data = json.loads(result_text)
            
            return data.get("articles", [])

        except Exception as e:
            logger.error(f"Ошибка при анализе страницы с помощью Mistral: {e}")
            return []
