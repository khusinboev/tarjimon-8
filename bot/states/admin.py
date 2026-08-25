from aiogram.fsm.state import StatesGroup, State


class AdminStates(StatesGroup):
    waiting_channel_add_button_text = State()
    waiting_channel_add_username = State()
    waiting_channel_add_button_url = State()
    waiting_channel_remove = State()
    waiting_broadcast_message = State()
    waiting_broadcast_test_confirm = State()
    waiting_broadcast_peak_confirm = State()
    waiting_user_query = State()
    waiting_user_limit = State()
    waiting_user_tts_limit = State()
    waiting_user_image_limit = State()

    waiting_global_translation_limit = State()
    waiting_global_tts_limit = State()
    waiting_global_image_free_limit = State()
    waiting_global_image_vip_limit = State()
