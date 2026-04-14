#!/bin/bash

# Скрипт для запуска бота с автоперезапуском

cd "$(dirname "$0")"

echo "🎵 Запуск GG_Loader Bot..."

# Проверяем наличие .env файла
if [ ! -f .env ]; then
    echo "⚠️  Файл .env не найден. Копируем из .env.example..."
    cp .env.example .env
    echo "❗ Отредактируйте .env и добавьте ваш токен!"
    exit 1
fi

# Активируем виртуальное окружение если есть
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Запускаем бота с автоперезапуском
while true; do
    python bot.py
    
    # Если бот упал, ждем 5 секунд и перезапускаем
    echo "⚠️  Бот остановился. Перезапуск через 5 секунд..."
    sleep 5
done
