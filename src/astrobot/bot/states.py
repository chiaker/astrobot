from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_city = State()
    choosing_city = State()
    confirming = State()
    waiting_for_name = State()
    choosing_gender = State()
    choosing_astro_terms = State()
    final_confirm = State()


class AskingQuestion(StatesGroup):
    waiting_for_text = State()


class PushSetup(StatesGroup):
    waiting_for_city = State()
    choosing_hour = State()


class PaymentFlow(StatesGroup):
    waiting_for_email = State()


class SupportFlow(StatesGroup):
    waiting_for_text = State()
