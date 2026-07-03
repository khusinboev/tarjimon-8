from aiogram.fsm.state import StatesGroup, State


class AdminStates(StatesGroup):
    waiting_channel_add_button_text = State()
    waiting_channel_add_username = State()
    waiting_channel_add_button_url = State()
    waiting_channel_remove = State()
    waiting_broadcast_mode = State()
    waiting_broadcast_message = State()
    waiting_broadcast_cancel_id = State()
