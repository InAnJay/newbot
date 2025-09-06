# Руководство по запуску

## ⚠️ Важно: Используйте виртуальное окружение

Чтобы избежать конфликтов с другими Python-пакетами на вашем компьютере, настоятельно рекомендуется установить зависимости в виртуальное окружение.

```bash
# 1. Создайте виртуальное окружение (если у вас его еще нет)
python -m venv venv

# 2. Активируйте его
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 3. Теперь установите зависимости (уже внутри окружения)
pip install -r requirements.txt
```
После этого продолжайте с шага 2 в "Быстром старте".

## 🚀 Быстрый старт

### 1. Получите необходимые токены

#### Telegram Bot Token
1.  Найдите @BotFather в Telegram.
2.  Отправьте `/newbot` и следуйте инструкциям.
3.  Скопируйте полученный токен.

#### OpenAI API Key
1.  Зарегистрируйтесь на [platform.openai.com](https://platform.openai.com).
2.  Перейдите в раздел `API Keys`.
3.  Создайте новый API ключ и скопируйте его.

#### Telegram User ID
1.  Найдите @userinfobot в Telegram.
2.  Отправьте `/start`.
3.  Скопируйте ваш ID.

### 2. Настройка проекта

```bash
# Установите зависимости (внутри виртуального окружения)
pip install -r requirements.txt

# Запустите скрипт первоначальной настройки
# (создаст .env и базу данных)
python setup.py
```

### 3. Заполните конфигурацию

Отредактируйте файл `.env`, который был создан на предыдущем шаге, и вставьте ваши токены:

```env
# Токен вашего Telegram бота
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# Ключ для OpenAI API
OPENAI_API_KEY=sk-1234567890abcdefghijklmnopqrstuvwxyz

# Ваш ID пользователя в Telegram для администрирования
ADMIN_USER_ID=123456789
```

### 4. Запуск бота

```bash
# Запустить главного бота
python main.py
```
Либо используйте скрипты для вашей ОС:
-   **Windows**: запустите `START.bat`
-   **Linux/Mac**: выполните в терминале `./start.sh`

После запуска бот начнет работать и проверять источники новостей согласно расписанию в `config.py`.

## 🐳 Запуск в Docker (Альтернативный способ)

Если вы предпочитаете Docker, вы можете собрать и запустить бота в контейнере.

### 1. Создайте `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

### 2. Создайте `docker-compose.yml`

```yaml
version: '3.8'

services:
  news-bot:
    build: .
    container_name: news-bot
    restart: unless-stopped
    volumes:
      - ./news_bot.db:/app/news_bot.db
    env_file:
      - .env
```
*Примечание: убедитесь, что ваш `.env` файл заполнен перед запуском.*

### 3. Запустите контейнер

```bash
docker-compose up -d --build
```

### 4. Просмотр логов
```bash
docker-compose logs -f
```
