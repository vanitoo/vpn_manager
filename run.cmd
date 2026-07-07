@echo off
chcp 65001 >nul
set PYTHONUTF8=1

:: Проверка наличия .venv
if not exist ".venv" (
    echo Создание .venv...
    python -m venv .venv
    echo Установка зависимостей...
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    echo Активация .venv...
    call .venv\Scripts\activate.bat
)

git pull

:: Запуск бота
python -m app.main
