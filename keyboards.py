from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def start_test_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Начать тест", callback_data="start_test")]
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
            ]
        ]
    )
