from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_city = State()
    choosing_city = State()
    confirming = State()


class AskingQuestion(StatesGroup):
    waiting_for_text = State()
