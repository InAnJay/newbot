#!/bin/bash

# --- Скрипт для запуска бота новостей о маркетплейсах в Linux/macOS ---

# Цветные выводы для наглядности
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}    Бот новостей о маркетплейсах       ${NC}"
echo -e "${GREEN}========================================${NC}"
echo

# Проверка, существует ли venv
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Виртуальное окружение не найдено. Создаю...${NC}"
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo -e "${RED}ОШИБКА: Не удалось создать виртуальное окружение. Убедитесь, что python3 и пакет python3-venv установлены.${NC}"
        exit 1
    fi
fi

echo "Активирую виртуальное окружение..."
source venv/bin/activate

echo "Устанавливаю/обновляю зависимости из requirements.txt..."
pip install -r requirements.txt --upgrade --quiet
if [ $? -ne 0 ]; then
    echo -e "${RED}ОШИБКА: Не удалось установить зависимости!${NC}"
    exit 1
fi
echo -e "${GREEN}Зависимости успешно установлены.${NC}"
echo

# Проверка конфигурации .env
echo "Проверка конфигурации..."
if ! python3 -c "from dotenv import load_dotenv; import os; load_dotenv(); assert os.getenv('TELEGRAM_BOT_TOKEN'), 'TELEGRAM_BOT_TOKEN не найден'; assert os.getenv('MISTRAL_API_KEY'), 'MISTRAL_API_KEY не найден'; assert os.getenv('OPENAI_API_KEY'), 'OPENAI_API_KEY не найден'; assert os.getenv('ADMIN_USER_ID'), 'ADMIN_USER_ID не найден'; assert os.getenv('TARGET_CHANNEL_ID'), 'TARGET_CHANNEL_ID не найден'" > /dev/null 2>&1; then
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}ВНИМАНИЕ: Файл .env не найден!${NC}"
        echo "Создаю .env из .env.example..."
        cp .env.example .env
        echo -e "${GREEN}Файл .env создан.${NC}"
    fi
    echo
    echo -e "${RED}ПОЖАЛУЙСТА, ЗАПОЛНИТЕ .env файл!${NC}"
    echo
    echo -e "💡 Для получения Telegram ID отправьте /start боту @userinfobot"
    exit 1
fi

echo -e "${GREEN}Конфигурация в порядке.${NC}"
echo

echo -e "Запускаю бота..."
echo "(Для остановки нажмите Ctrl+C)"
echo

python3 main.py

echo
echo -e "${YELLOW}Бот остановлен.${NC}"
