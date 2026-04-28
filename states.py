from aiogram.fsm.state import StatesGroup, State


class TestState(StatesGroup):
    answering = State()