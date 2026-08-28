FROM denoland/deno:debian-2.6.9 AS deno

FROM python:3.11-slim

# yt-dlp requires a supported JavaScript runtime for current YouTube player
# challenges. Deno is the runtime recommended by yt-dlp and is enabled by
# default; Debian's nodejs package can be older than the required Node 22.
COPY --from=deno /usr/bin/deno /usr/local/bin/deno
ENV YTDLP_JS_RUNTIME=deno

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY . .

# Создаем директории для временных файлов, inline-кэша и истории
RUN mkdir -p temp/inline_media_cache data

EXPOSE 8080

# Запускаем бота
CMD ["python", "bot.py"]
