import logging
from pathlib import Path
from typing import Optional

from aiogram.types import FSInputFile, Message

logger = logging.getLogger(__name__)

MEDIA_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "media"
PROGRESS_ANIMATION_PATH = MEDIA_ASSETS_DIR / "download_progress.mp4"
ERROR_IMAGE_PATHS = {
    "video": MEDIA_ASSETS_DIR / "download_error_video.png",
    "audio": MEDIA_ASSETS_DIR / "download_error_audio.png",
    "circle": MEDIA_ASSETS_DIR / "download_error_circle.png",
}


async def send_media_progress(message: Message, text: str) -> Message:
    """Sends the shared MP4 animation and falls back to an ordinary message."""
    if PROGRESS_ANIMATION_PATH.is_file():
        try:
            return await message.answer_animation(
                animation=FSInputFile(PROGRESS_ANIMATION_PATH),
                caption=text,
                parse_mode="HTML",
            )
        except Exception as error:
            logger.warning("Unable to send progress animation: %s", error)
    return await message.answer(text, parse_mode="HTML")


async def close_media_status(status_message: Optional[Message]) -> None:
    if not status_message:
        return
    try:
        await status_message.delete()
    except Exception as error:
        logger.debug("Unable to delete media status message: %s", error)


async def send_media_error(
    message: Message,
    status_message: Optional[Message],
    media_kind: str,
    text: str,
    reply_markup=None,
) -> Message:
    """Replaces a progress response with the matching media error illustration."""
    await close_media_status(status_message)
    image_path = ERROR_IMAGE_PATHS.get(media_kind)
    if image_path and image_path.is_file():
        try:
            return await message.answer_photo(
                photo=FSInputFile(image_path),
                caption=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception as error:
            logger.warning("Unable to send %s error image: %s", media_kind, error)
    return await message.answer(
        text,
        reply_markup=reply_markup,
        parse_mode="HTML",
    )
