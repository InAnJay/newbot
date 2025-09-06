@echo off
chcp 65001 > nul
echo ========================================
echo    Бот новостей о маркетплейсах
echo ========================================
echo.

REM Проверка, существует ли venv
if not exist venv (
    echo [93mВиртуальное окружение не найдено. Создаю...[0m
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [91mОШИБКА: Не удалось создать виртуальное окружение. Убедитесь, что Python установлен и доступен.[0m
        pause
        exit /b 1
    )
)

echo Активирую виртуальное окружение...
call venv\Scripts\activate

echo Устанавливаю/обновляю зависимости из requirements.txt...
pip install -r requirements.txt --upgrade --quiet
if %errorlevel% neq 0 (
    echo [91mОШИБКА: Не удалось установить зависимости![0m
    pause
    exit /b 1
)
echo [92mЗависимости успешно установлены.[0m
echo.

REM Проверка конфигурации .env
echo Проверка конфигурации...
python -c "from dotenv import load_dotenv; import os; load_dotenv(); assert os.getenv('TELEGRAM_BOT_TOKEN'), 'TELEGRAM_BOT_TOKEN не найден'; assert os.getenv('MISTRAL_API_KEY'), 'MISTRAL_API_KEY не найден'; assert os.getenv('OPENAI_API_KEY'), 'OPENAI_API_KEY не найден'; assert os.getenv('ADMIN_USER_ID'), 'ADMIN_USER_ID не найден'; assert os.getenv('TARGET_CHANNEL_ID'), 'TARGET_CHANNEL_ID не найден'"
if %errorlevel% neq 0 (
    if not exist .env (
        echo [93mВНИМАНИЕ: Файл .env не найден![0m
        echo Создаю .env из .env.example...
        copy .env.example .env > nul
        echo [92mФайл .env создан.[0m
    )
    echo.
    echo [91mПОЖАЛУЙСТА, ЗАПОЛНИТЕ .env файл![0m
    echo.
    echo [96m💡 Для получения Telegram ID отправьте /start боту @userinfobot[0m
    pause
    exit /b 1
)

echo [92mКонфигурация в порядке.[0m
echo.

echo [94mЗапускаю бота...[0m
echo (Для остановки нажмите Ctrl+C)
echo.

python main.py

echo.
echo [93mБот остановлен.[0m
pause
