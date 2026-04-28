import asyncio
import logging
import os
from dataclasses import dataclass
from typing import List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from openai import AsyncOpenAI
from dotenv import load_dotenv

from states import TestState
from test_data import SHORT_TEST
from keyboards import test_choice_keyboard, answer_keyboard
from db import (
    connect_db,
    close_db,
    ensure_user,
    save_message,
    load_recent_messages,
    get_user,
    get_profile,
    upsert_profile,
    set_test_completed
)

load_dotenv()
# print("ENV TEST:", os.getenv("DATABASE_URL"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("deepseek-telegram-bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "Ты поддерживающий, тёплый и внимательный собеседник. Отвечай по-русски."
    "Используй HTML-разметку Telegram:"
    "- <b>жирный</b>"
    "- <i>курсив</i>"
    "- <u>подчёркнутый</u>"
    "не нужно отвечать большим текстом, МАКСИМУМ 20 предложений"


)

MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
THINKING_MODE = os.getenv("DEEPSEEK_THINKING_MODE", "false").lower() in {"1", "true", "yes", "on"}
REASONING_EFFORT = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "700"))
TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.7"))

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()


CRISIS_KEYWORDS = {
    "суицид",
    "самоубий",
    "хочу умереть",
    "не хочу жить",
    "покончить с собой",
    "самоповреж",
    "вскрыть",
    "убить себя",
}


@dataclass
class LLMResult:
    text: str
    raw_reasoning: str | None = None


def is_crisis_message(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in CRISIS_KEYWORDS)


async def build_messages(user_id: int, history: List[dict], user_text: str) -> List[dict]:
    system_prompt = await build_system_prompt(user_id)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    return messages


async def ask_deepseek(user_id: int, history: List[dict], user_text: str) -> LLMResult:
    messages = await build_messages(user_id, history, user_text)

    extra_body = {"thinking": {"type": "enabled" if THINKING_MODE else "disabled"}}

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        # reasoning_effort=REASONING_EFFORT,
        extra_body=extra_body
    )

    choice = response.choices[0].message
    content = choice.content or ""
    reasoning = getattr(choice, "reasoning_content", None)

    return LLMResult(text=content.strip(), raw_reasoning=reasoning)


def crisis_reply() -> str:
    return (
        "Мне очень жаль, что тебе сейчас так тяжело. Я не хочу оставлять это без внимания. "
        "Если есть риск, что ты можешь навредить себе прямо сейчас, позвони в экстренные службы своего региона "
        "или попроси близкого человека побыть рядом с тобой немедленно. "
        "Напиши мне, что именно происходит, и я помогу тебе удержаться в этом моменте и пройти его шаг за шагом."
    )

def profile_to_prompt(profile) -> str:
    if not profile:
        return ""

    parts = []

    introversion = profile["introversion"]
    need_support = profile["need_support"]
    directness = profile["directness"]
    detail_preference = profile["detail_preference"]

    if introversion is not None and introversion >= 0.7:
        parts.append("Пользователь склонен к интроверсии: не дави, не будь навязчивым.")

    if need_support is not None and need_support >= 0.7:
        parts.append("Пользователю важна эмоциональная поддержка: отвечай мягко и с эмпатией.")

    if directness is not None and directness >= 0.7:
        parts.append("Пользователь предпочитает прямые ответы: говори по делу, без воды.")

    if detail_preference is not None:
        if detail_preference >= 0.75:
            parts.append("Иногда можно давать более подробные объяснения, но без перегрузки.")
        elif detail_preference >= 0.4:
            parts.append("Давай умеренно подробные ответы, без лишней воды.")
        else:
            parts.append("Отвечай кратко и по делу, избегай длинных объяснений.")

    return "\n".join(parts)

async def build_system_prompt(user_id: int) -> str:
    base_prompt = SYSTEM_PROMPT

    profile = await get_profile(user_id)
    profile_prompt = profile_to_prompt(profile)

    if profile_prompt:
        return base_prompt + "\n\nПрофиль пользователя:\n" + profile_prompt

    return base_prompt

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return

    await ensure_user(message.from_user.id, message.from_user.username)

    user = await get_user(message.from_user.id)
    if user and not user["test_completed"]:
        await message.answer(
            "Привет. Я на связи.\n\n"
            "Сначала выбери формат теста, чтобы я лучше подстраивался под тебя:",
            reply_markup=test_choice_keyboard(),
        )
        return

    await message.answer(
        "Привет. Я на связи и готов поддержать разговор.\n\n"
        "Напиши, что у тебя происходит, и я отвечу спокойно и без давления."
    )


@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    # История теперь хранится в БД, поэтому тут можно оставить только служебный сброс,
    # если позже добавите отдельную таблицу для статуса теста.
    await message.answer("Команда сброса пока не подключена к отдельному статусу.")


@router.message(F.text)
async def handle_text(message: Message) -> None:
    if not message.from_user:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    if not user_text:
        await message.answer("Напиши сообщение текстом, и я отвечу.")
        return

    await ensure_user(user_id, message.from_user.username)

    if is_crisis_message(user_text):
        await save_message(user_id, "user", user_text)
        await message.answer(crisis_reply())
        await save_message(user_id, "assistant", crisis_reply())
        return

    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        history = await load_recent_messages(user_id, limit=12)
        result = await ask_deepseek(user_id, history, user_text)
    except Exception as exc:
        logger.exception("DeepSeek request failed: %s", exc)
        await message.answer(
            "Сейчас не получилось получить ответ от модели. Попробуй ещё раз через несколько секунд."
        )
        return

    reply = result.text or "Я не смог сформировать ответ. Попробуй переформулировать сообщение."

    await save_message(user_id, "user", user_text)
    await save_message(user_id, "assistant", reply)

    await message.answer(reply, parse_mode="HTML")

@router.callback_query(F.data.startswith("test_"))
async def handle_test_choice(callback, state: FSMContext):
    user_id = callback.from_user.id
    choice = callback.data

    mode = "short" if choice == "test_short" else "full"

    # пока используем только короткий тест
    questions = SHORT_TEST

    await state.update_data(
        mode=mode,
        questions=questions,
        answers=[],
        index=0,
    )

    await state.set_state(TestState.answering)
    await callback.message.edit_text(
        "Начинаем тест.\n\n" + questions[0][0] + "\n\n1 — не согласен\n5 — полностью согласен",
        reply_markup=answer_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("ans_"), TestState.answering)
async def handle_answer(callback, state: FSMContext):
    data = await state.get_data()

    index = data["index"]
    questions = data["questions"]
    answers = data["answers"]

    value = int(callback.data.split("_")[1])
    answers.append(value)

    index += 1

    if index >= len(questions):
        # --- подсчёт ---
        result = {
            "introversion": 0,
            "need_support": 0,
            "directness": 0,
            "detail_preference": 0,
        }

        counts = {k: 0 for k in result}

        for (question, key), answer in zip(questions, answers):
            result[key] += answer
            counts[key] += 1

        for key in result:
            if counts[key] > 0:
                result[key] = (result[key] / counts[key] - 1) / 4

        await upsert_profile(
            callback.from_user.id,
            result["introversion"],
            result["need_support"],
            result["directness"],
            result["detail_preference"],
        )

        await set_test_completed(callback.from_user.id, data["mode"])

        await state.clear()

        await callback.message.edit_text("Тест завершён. Теперь я лучше понимаю, как с тобой общаться.")

        await callback.answer()
        return

    # следующий вопрос
    await state.update_data(index=index, answers=answers)

    next_question = questions[index][0]

    await callback.message.edit_text(next_question, reply_markup=answer_keyboard())
    await callback.answer()

async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    await connect_db()

    try:
        dp.include_router(router)
        logger.info("Starting bot with model=%s thinking=%s", MODEL_NAME, THINKING_MODE)
        await dp.start_polling(bot)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())