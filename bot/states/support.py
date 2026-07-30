from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    """Adminga murojaat. Bir qadamli: xabarni kutamiz, yuboramiz, tugadi."""

    waiting_message = State()
