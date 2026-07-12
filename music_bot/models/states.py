from aiogram.fsm.state import StatesGroup, State

class MediaStates(StatesGroup):
    # Состояния для загрузки видео/аудио
    waiting_for_video_link = State()
    waiting_for_audio_link = State()
    waiting_for_extract_link = State()
    waiting_for_extract_format = State()  # Новое состояние для выбора формата (MP3 / Voice)
    
    # Состояния для наложения обложки
    waiting_for_audio_file = State()
    waiting_for_cover = State()
    waiting_for_track_info = State()
    waiting_for_channel_link = State()  # Новое состояние для ссылки на канал