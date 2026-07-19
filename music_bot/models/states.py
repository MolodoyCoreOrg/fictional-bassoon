from aiogram.fsm.state import State, StatesGroup

class DownloadVideo(StatesGroup):
    waiting_for_url = State()
    waiting_for_quality = State()

class DownloadMusic(StatesGroup):
    waiting_for_url = State()
    waiting_for_title = State()
    waiting_for_artist = State()