from aiogram.fsm.state import StatesGroup, State


class TestState(StatesGroup):
    waiting_age = State()
    choosing_gender = State()
    answering = State()