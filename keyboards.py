from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def start_test_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Начать тест", callback_data="start_test")]
        ]
    )


def gender_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мужской", callback_data="gender_male"),
                InlineKeyboardButton(text="Женский", callback_data="gender_female"),
            ],
            [
                InlineKeyboardButton(text="Другой / не указывать", callback_data="gender_other")
            ],
            [
                InlineKeyboardButton(text="⬅️ Предыдущий вопрос", callback_data="nav_back")
            ],
        ]
    )


def answer_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="ans_0"),
                InlineKeyboardButton(text="2", callback_data="ans_1"),
                InlineKeyboardButton(text="3", callback_data="ans_2"),
                InlineKeyboardButton(text="4", callback_data="ans_3"),
                InlineKeyboardButton(text="5", callback_data="ans_4"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Предыдущий вопрос", callback_data="nav_back")
            ],
        ]
    )