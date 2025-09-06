import logging
from typing import Optional
import openai
import requests
import os
import uuid

from config import OPENAI_API_KEY, IMAGE_SIZE, IMAGE_QUALITY

logger = logging.getLogger(__name__)

class OpenAIClient:
    """
    Клиент для работы с API OpenAI, используется ИСКЛЮЧИТЕЛЬНО для генерации изображений.
    """
    def __init__(self):
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY не установлен в переменных окружения")

        self.client = openai.OpenAI(api_key=OPENAI_API_KEY)
        self.dalle_model = "dall-e-3"
        
        # Создаем папку для изображений, если ее нет
        self.images_dir = "images"
        os.makedirs(self.images_dir, exist_ok=True)

    def generate_image_prompt(self, title: str, content: str) -> str:
        """Создает промпт для DALL-E на основе заголовка и контента."""
        prompt = (
            f"News article illustration: '{title}'. "
            f"Content summary: {content[:500]}. "
            "Style: photorealistic, high detail, professional illustration for a news article."
        )
        return prompt

    def generate_image(self, title: str, content: str) -> Optional[str]:
        """Сгенерировать изображение, скачать его и вернуть локальный путь."""
        try:
            image_prompt = self.generate_image_prompt(title, content)
            logger.info(f"Генерация изображения с промптом: {image_prompt}")

            response = self.client.images.generate(
                model=self.dalle_model,
                prompt=image_prompt,
                size=IMAGE_SIZE,
                quality=IMAGE_QUALITY,
                n=1
            )

            image_url = response.data[0].url
            
            # Скачиваем изображение
            image_response = requests.get(image_url, stream=True)
            image_response.raise_for_status()
            
            # Генерируем уникальное имя файла
            file_name = f"{uuid.uuid4()}.png"
            file_path = os.path.join(self.images_dir, file_name)
            
            with open(file_path, 'wb') as f:
                for chunk in image_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logger.info(f"Изображение успешно скачано и сохранено по пути: {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Ошибка при генерации или скачивании изображения через OpenAI: {e}")
            return None
