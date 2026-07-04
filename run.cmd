@echo off

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

:: Запуск бота
python -m app.main
