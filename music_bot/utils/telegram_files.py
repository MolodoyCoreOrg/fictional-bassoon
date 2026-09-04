"""Download incoming Telegram media without mixing API paths and local paths."""

import asyncio
import logging
from pathlib import Path, PurePosixPath, PureWindowsPath

import aiofiles
from aiohttp import ClientError
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from utils.config import (
    TELEGRAM_API_FILE_ROOT,
    TELEGRAM_BOT_FILE_ROOT,
    TELEGRAM_DOWNLOAD_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)
DOWNLOAD_ATTEMPTS = 3


class TelegramDownloadError(RuntimeError):
    """A download failure with a safe, actionable message for the user."""


def _local_source(file_path: str) -> Path | None:
    # A --local Bot API returns an absolute path even when outgoing files are
    # sent by multipart (TELEGRAM_LOCAL_FILE_MODE=false).
    if not (PurePosixPath(file_path).is_absolute()
            or PureWindowsPath(file_path).is_absolute()):
        return None

    if TELEGRAM_API_FILE_ROOT and TELEGRAM_BOT_FILE_ROOT:
        path_type = (
            PureWindowsPath
            if PureWindowsPath(file_path).drive
            else PurePosixPath
        )
        try:
            relative = path_type(file_path).relative_to(
                path_type(TELEGRAM_API_FILE_ROOT)
            )
        except ValueError:
            raise TelegramDownloadError(
                "❌ Сервер бота не видит папку файлов Telegram. "
                "Сообщите администратору бота."
            ) from None
        root = Path(TELEGRAM_BOT_FILE_ROOT).resolve()
        source = root.joinpath(*relative.parts).resolve()
        if not source.is_relative_to(root):
            raise TelegramDownloadError("❌ Telegram вернул некорректный путь файла.")
        return source
    return Path(file_path)


async def _copy_local_file(source: Path, destination: Path) -> None:
    # Copy, never move: the original belongs to the Bot API cache.
    async with aiofiles.open(source, "rb") as reader:
        async with aiofiles.open(destination, "wb") as writer:
            while chunk := await reader.read(65536):
                await writer.write(chunk)


async def download_telegram_file(bot, file_id: str, destination: str) -> None:
    """Save a complete file, refreshing getFile and retrying transient failures."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        for attempt in range(DOWNLOAD_ATTEMPTS):
            try:
                file = await bot.get_file(
                    file_id,
                    request_timeout=TELEGRAM_DOWNLOAD_TIMEOUT_SECONDS,
                )
                if not file.file_path:
                    raise TelegramDownloadError(
                        "❌ Telegram не вернул файл для скачивания. "
                        "Отправьте файл ещё раз."
                    )

                source = _local_source(file.file_path)
                if source is not None:
                    try:
                        await _copy_local_file(source, partial)
                    except OSError:
                        logger.error(
                            "Telegram local file is unavailable; check Bot API cache "
                            "volume and TELEGRAM_API_FILE_ROOT/TELEGRAM_BOT_FILE_ROOT"
                        )
                        raise TelegramDownloadError(
                            "❌ Сервер бота не может прочитать файл из Telegram. "
                            "Сообщите администратору бота."
                        ) from None
                else:
                    await bot.download_file(
                        file.file_path,
                        destination=partial,
                        timeout=TELEGRAM_DOWNLOAD_TIMEOUT_SECONDS,
                    )

                size = partial.stat().st_size
                expected_size = getattr(file, "file_size", None)
                if size == 0 or (expected_size is not None and size != expected_size):
                    raise TelegramDownloadError(
                        "❌ Файл из Telegram загрузился не полностью. "
                        "Отправьте его ещё раз."
                    )
                partial.replace(target)
                return
            except TelegramBadRequest as error:
                if "file is too big" in error.message.lower():
                    raise TelegramDownloadError(
                        "❌ Telegram не позволяет боту скачать этот файл: "
                        "у облачного Bot API лимит скачивания 20 МБ. "
                        "Отправьте файл поменьше или ссылку."
                    ) from None
                raise TelegramDownloadError(
                    "❌ Telegram не предоставил файл для скачивания. "
                    "Отправьте файл ещё раз."
                ) from None
            except (TelegramNetworkError, ClientError, asyncio.TimeoutError) as error:
                # Do not log exception URLs: they can contain the bot token.
                logger.warning(
                    "Telegram download attempt %s/%s failed (%s)",
                    attempt + 1, DOWNLOAD_ATTEMPTS, type(error).__name__,
                )
                if attempt + 1 == DOWNLOAD_ATTEMPTS:
                    raise TelegramDownloadError(
                        "❌ Не удалось получить файл из Telegram из-за сбоя связи. "
                        "Попробуйте отправить его ещё раз немного позже."
                    ) from None
                partial.unlink(missing_ok=True)
                await asyncio.sleep(attempt + 1)
    finally:
        partial.unlink(missing_ok=True)
