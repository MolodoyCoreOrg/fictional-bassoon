from aiogram.fsm.state import State, StatesGroup

class DownloadVideo(StatesGroup):
    waiting_for_url = State()
    waiting_for_quality = State()

class DownloadMusic(StatesGroup):
    waiting_for_url = State()
    waiting_for_title = State()
    waiting_for_artist = State()

class MediaStates(StatesGroup):
    waiting_for_video_link = State()
    waiting_for_audio_link = State()
    waiting_for_extract_link = State()
    waiting_for_extract_format = State()
    waiting_for_audio_file = State()
    waiting_for_cover = State()
    waiting_for_track_info = State()
    waiting_for_channel_link = State()
