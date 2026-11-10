from aiogram.fsm.state import State, StatesGroup


class SupportStates(StatesGroup):
    """Adminga murojaat. Bir qadamli: xabarni kutamiz, yuboramiz, tugadi."""

    waiting_message = State()


class DonateStates(StatesGroup):
    """Homiylik: faqat "boshqa miqdor" yo'li holat talab qiladi."""

    waiting_custom_amount = State()
