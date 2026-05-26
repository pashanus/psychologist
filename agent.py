import asyncio
import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from dotenv import load_dotenv
from openai import AsyncOpenAI

from db import (
    close_db,
    connect_db,
    ensure_user,
    get_profile,
    get_summary,
    get_user,
    get_users_for_nudges,
    load_recent_messages,
    save_demographics,
    save_message,
    set_test_completed,
    touch_user_activity,
    upsert_profile,
    update_last_nudge,
    update_summary,
)
from keyboards import answer_keyboard, gender_keyboard, start_test_keyboard
from states import TestState
from test_data import TEST

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("deepseek-telegram-bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
SUMMARY_MODEL_NAME = os.getenv("DEEPSEEK_SUMMARY_MODEL", MODEL_NAME)

THINKING_MODE = os.getenv("DEEPSEEK_THINKING_MODE", "false").lower() in {"1", "true", "yes", "on"}
MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "700"))
TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.8"))

SUMMARY_MAX_TOKENS = int(os.getenv("DEEPSEEK_SUMMARY_MAX_TOKENS", "100"))
SUMMARY_TEMPERATURE = float(os.getenv("DEEPSEEK_SUMMARY_TEMPERATURE", "0.1"))
SUMMARY_MIN_CHARS = int(os.getenv("DEEPSEEK_SUMMARY_MIN_CHARS", "40"))
SUMMARY_MAX_CHARS = int(os.getenv("SUMMARY_MAX_CHARS", "700"))

NUDGE_AFTER_HOURS = float(os.getenv("NUDGE_AFTER_HOURS", "24"))
NUDGE_REPEAT_HOURS = float(os.getenv("NUDGE_REPEAT_HOURS", "24"))
NUDGE_CHECK_INTERVAL_SECONDS = int(os.getenv("NUDGE_CHECK_INTERVAL_SECONDS", "1800"))


SYSTEM_PROMPT = """
Ты — поддерживающий психологически ориентированный собеседник (мужчина).
Не ставь диагнозов, не используй клинические ярлыки и не приписывай человеку расстройства.

Твоя задача — помогать пользователю прояснять состояние, снижать напряжение и находить следующий небольшой шаг.
Если запрос не относится к психологии, эмоциям, отношениям, стрессу, самооценке или самочувствию — вежливо откажись и предложи вернуться к психологической теме.

Не критикуй пользователя, только помогай.
Используй профиль пользователя, чтобы адаптировать стиль ответа.
Не упоминай сам профиль напрямую.
Не пиши большого текста. Не нагружай пользователя. Даже если пользователь любит подробности — это не значит, что нужно расписывать каждое слово. Не задавай сразу много вопросов.
Язык: русский.

Используй HTML-разметку Telegram:
- <b>жирный</b>
- <i>курсив</i>
- <u>подчёркнутый</u>

НЕЛЬЗЯ использовать теги <ul>, <li>, <br>, <div>
""".strip()

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


bot: Bot | None = None
dp = Dispatcher(storage=MemoryStorage())
router = Router()

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com",
)


def _ensure_bot() -> Bot:
    if bot is None:
        raise RuntimeError("Bot is not initialized")
    return bot


def is_crisis_message(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in CRISIS_KEYWORDS)


def _normalize_ts(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean_summary(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    cleaned = cleaned.replace("•", "").strip()
    return cleaned


def profile_to_prompt(profile) -> str:
    if not profile:
        return ""

    parts = []

    def level(value, low=0.3, high=0.7):
        if value is None:
            return "unknown"
        if value < low:
            return "low"
        if value > high:
            return "high"
        return "medium"

    lvl = level(profile.get("introversion"))
    if lvl == "high":
        parts.append("Пользователь предпочитает спокойное, ненавязчивое общение.")
    elif lvl == "low":
        parts.append("Пользователь комфортно чувствует себя в активном взаимодействии.")

    lvl = level(profile.get("need_support"))
    if lvl == "high":
        parts.append("Ему важны эмпатия, поддержка и бережный тон.")
    elif lvl == "low":
        parts.append("Он меньше нуждается в эмоциональной поддержке, можно говорить более нейтрально.")

    lvl = level(profile.get("directness"))
    if lvl == "high":
        parts.append("Предпочитает прямые и ясные формулировки.")
    elif lvl == "low":
        parts.append("Лучше использовать мягкие и осторожные формулировки.")

    lvl = level(profile.get("detail_preference"))
    if lvl == "high":
        parts.append("Любит структурированные и подробные объяснения.")
    elif lvl == "low":
        parts.append("Предпочитает краткие ответы без лишних деталей.")

    lvl = level(profile.get("anxiety"))
    if lvl == "high":
        parts.append("Склонен к тревожности, важно снижать напряжение и давать ощущение безопасности.")
    elif lvl == "low":
        parts.append("Обычно эмоционально стабилен.")

    lvl = level(profile.get("self_esteem"))
    if lvl == "low":
        parts.append("Склонен к самокритике, важно избегать давления и оценочных суждений.")
    elif lvl == "high":
        parts.append("Уверен в себе.")

    lvl = level(profile.get("emotional_sensitivity"))
    if lvl == "high":
        parts.append("Чувствителен к словам, важно быть аккуратным в формулировках.")

    lvl = level(profile.get("trust"))
    if lvl == "low":
        parts.append("Может быть насторожен, важно говорить прозрачно и без скрытых смыслов.")
    elif lvl == "high":
        parts.append("Склонен доверять.")

    lvl = level(profile.get("rumination"))
    if lvl == "high":
        parts.append("Склонен зацикливаться на мыслях, полезны техники переключения.")

    lvl = level(profile.get("control_need"))
    if lvl == "high":
        parts.append("Ему важно ощущение контроля, полезны чёткие и предсказуемые шаги.")

    return "\n".join(parts)


async def build_system_prompt(user_id: int) -> str:
    base_prompt = SYSTEM_PROMPT
    profile = await get_profile(user_id)
    profile_prompt = profile_to_prompt(profile)

    if profile_prompt:
        return base_prompt + "\n\nПрофиль пользователя:\n" + profile_prompt

    return base_prompt


async def build_messages(user_id: int, history: List[dict], user_text: str) -> List[dict]:
    system_prompt = await build_system_prompt(user_id)
    summary = await get_summary(user_id)

    messages = [{"role": "system", "content": system_prompt}]

    if summary:
        messages.append(
            {
                "role": "system",
                "content": f"Краткий контекст диалога:\n{summary}",
            }
        )

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
        extra_body=extra_body,
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


async def summarize_dialogue(user_id: int, history: list[dict]) -> str:
    old_summary = await get_summary(user_id)

    recent_history = history[-12:] if len(history) > 12 else history

    messages = [
        {
            "role": "system",
            "content": (
                "Ты обновляешь долговременную память о пользователе.\n\n"
                "Нужно сохранить именно психологически важный контекст, а не пересказ диалога.\n\n"
                "Сохраняй:\n"
                "- эмоциональные проблемы\n"
                "- повторяющиеся страхи\n"
                "- важные отношения\n"
                "- внутренние конфликты\n"
                "- триггеры агрессии или тревоги\n"
                "- важные события\n"
                "- устойчивые особенности личности\n\n"
                "НЕ сохраняй:\n"
                "- случайные мелочи\n"
                "- дословные фразы\n"
                "- одноразовые детали\n\n"
                "Пиши 2-4 коротких, полных предложений. "
                "Не обрывай мысль на середине. "
                "Не пиши списки и заголовки. "
                "Верни только готовый текст памяти."
            ),
        },
        {
            "role": "system",
            "content": f"Текущая память:\n{old_summary or 'пусто'}",
        },
        *recent_history,
        {
            "role": "user",
            "content": (
                "Обнови долговременную память о пользователе. "
                "Сохрани старую важную информацию и добавь новые важные детали. "
                "Верни только готовый текст памяти."
            ),
        },
    ]

    response = await client.chat.completions.create(
        model=SUMMARY_MODEL_NAME,
        messages=messages,
        max_tokens=SUMMARY_MAX_TOKENS,
        temperature=SUMMARY_TEMPERATURE,
        extra_body={"thinking": {"type": "disabled"}},
    )

    summary = _clean_summary(response.choices[0].message.content or "")
    summary = summary[:SUMMARY_MAX_CHARS]
    if len(summary) < SUMMARY_MIN_CHARS:
        return old_summary or ""

    return summary


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return

    await ensure_user(message.from_user.id, message.from_user.username)

    user = await get_user(message.from_user.id)
    if user and not user["test_completed"]:
        await message.answer(
            "Привет, я на связи.\n\n"
            "Сначала хочу задать пару коротких вопросов, чтобы подстроить стиль общения. "
            "Это займёт около 2 минут.",
            reply_markup=start_test_keyboard(),
        )
        return

    await message.answer("Привет. Я на связи и готов поддержать разговор.")


@router.message(Command("reset"))
async def cmd_reset(message: Message) -> None:
    await message.answer("Команда сброса пока не подключена к отдельному статусу.")


@router.message(F.text, StateFilter(None))
async def handle_text(message: Message) -> None:
    if not message.from_user:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()

    if not user_text:
        await message.answer("Напиши сообщение текстом, и я отвечу.")
        return

    await ensure_user(user_id, message.from_user.username)
    await touch_user_activity(user_id)

    if is_crisis_message(user_text):
        reply = crisis_reply()
        await save_message(user_id, "user", user_text)
        await save_message(user_id, "assistant", reply)
        await message.answer(reply)
        return

    bot_inst = _ensure_bot()
    await bot_inst.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    try:
        history = await load_recent_messages(user_id, limit=10)
        result = await ask_deepseek(user_id, history, user_text)
    except Exception as exc:
        logger.exception("DeepSeek request failed: %s", exc)
        await message.answer("Сейчас не получилось получить ответ. Попробуй ещё раз через несколько секунд.")
        return

    reply = result.text or "Я не смог сформировать ответ. Попробуй переформулировать сообщение."

    await save_message(user_id, "user", user_text)
    await save_message(user_id, "assistant", reply)

    await message.answer(reply, parse_mode="HTML")

    history_for_summary = history + [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": reply},
    ]

    try:
        summary = await summarize_dialogue(user_id, history_for_summary)
        if summary:
            old_summary = await get_summary(user_id)
            if summary != old_summary:
                await update_summary(user_id, summary)
            logger.info("SUMMARY UPDATED: %s", summary)
        else:
            logger.info("SUMMARY EMPTY, NOT SAVED")
    except Exception as exc:
        logger.exception("Summary update failed: %s", exc)


@router.callback_query(F.data == "start_test")
async def start_test(callback, state: FSMContext):
    if not callback.from_user:
        return

    await state.set_state(TestState.waiting_age)
    await state.update_data(answers=[], index=0, age=None, gender=None)

    await callback.message.edit_text(
        "Сколько тебе лет?\n\n"
        "Напиши число, например: 24"
    )
    await callback.answer()


@router.message(TestState.waiting_age, F.text)
async def handle_age(message: Message, state: FSMContext):
    if not message.from_user:
        return

    text = message.text.strip()

    if not text.isdigit():
        await message.answer("Напиши возраст числом, например: 24")
        return

    age = int(text)
    if age < 5 or age > 120:
        await message.answer("Укажи возраст от 5 до 120.")
        return

    await ensure_user(message.from_user.id, message.from_user.username)
    await save_demographics(message.from_user.id, age=age)

    await state.update_data(age=age)
    await state.set_state(TestState.choosing_gender)

    await message.answer(
        "Теперь укажи пол:",
        reply_markup=gender_keyboard(),
    )


@router.callback_query(TestState.choosing_gender, F.data.startswith("gender_"))
async def handle_gender(callback, state: FSMContext):
    if not callback.from_user:
        return

    gender = callback.data.replace("gender_", "", 1)

    await save_demographics(callback.from_user.id, gender=gender)
    await state.update_data(gender=gender)

    questions = TEST
    total = len(questions)

    await state.update_data(questions=questions, answers=[], index=0)
    await state.set_state(TestState.answering)

    await callback.message.edit_text(
        f"Вопрос 1/{total}\n\n"
        f"{questions[0]['question']}\n\n"
        "1 — не согласен\n5 — полностью согласен",
        reply_markup=answer_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "nav_back")
async def handle_back(callback, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()

    if current_state == TestState.choosing_gender.state:
        await state.set_state(TestState.waiting_age)
        await callback.message.edit_text(
            "Сколько тебе лет?\n\n"
            "Напиши число, например: 24"
        )
        await callback.answer()
        return

    if current_state == TestState.answering.state:
        index = data.get("index", 0)
        answers = data.get("answers", [])

        if index == 0:
            await state.set_state(TestState.choosing_gender)
            await callback.message.edit_text(
                "Укажи пол ещё раз:",
                reply_markup=gender_keyboard(),
            )
            await callback.answer()
            return

        index -= 1
        if answers:
            answers.pop()

        await state.update_data(index=index, answers=answers)

        question = TEST[index]
        total = len(TEST)

        await callback.message.edit_text(
            f"Вопрос {index + 1}/{total}\n\n"
            f"{question['question']}\n\n"
            "1 — не согласен\n5 — полностью согласен",
            reply_markup=answer_keyboard(),
        )
        await callback.answer()
        return

    await callback.answer()


@router.callback_query(F.data.startswith("ans_"), TestState.answering)
async def handle_answer(callback, state: FSMContext):
    if not callback.from_user:
        return

    data = await state.get_data()

    index = data["index"]
    questions = data["questions"]
    answers = data["answers"]

    value = int(callback.data.split("_")[1]) + 1
    answers.append(value)
    index += 1

    if index >= len(questions):
        result = {
            "introversion": 0,
            "need_support": 0,
            "directness": 0,
            "detail_preference": 0,
            "anxiety": 0,
            "self_esteem": 0,
            "emotional_sensitivity": 0,
            "trust": 0,
            "rumination": 0,
            "control_need": 0,
        }

        counts = {k: 0 for k in result}

        for q, answer in zip(questions, answers):
            key = q["key"]
            reverse = q.get("reverse", False)
            score = answer if not reverse else 6 - answer
            result[key] += score
            counts[key] += 1

        for key in result:
            if counts[key] > 0:
                result[key] = (result[key] / counts[key] - 1) / 4

        await upsert_profile(callback.from_user.id, result)
        await set_test_completed(callback.from_user.id, "single")

        await state.clear()
        await callback.message.edit_text(
            "Тест завершён, спасибо за уделённое время! Теперь можешь задавать вопрос."
        )
        await callback.answer()
        return

    await state.update_data(index=index, answers=answers)

    question = questions[index]
    total = len(questions)

    await callback.message.edit_text(
        f"Вопрос {index + 1}/{total}\n\n"
        f"{question['question']}\n\n"
        "1 — не согласен\n5 — полностью согласен",
        reply_markup=answer_keyboard(),
    )
    await callback.answer()


async def send_nudges():
    bot_inst = _ensure_bot()
    users = await get_users_for_nudges()
    now = datetime.now(timezone.utc)

    for user in users:
        last_message = user["last_user_message_at"]
        last_nudge = user["last_nudge_at"]

        if not last_message:
            continue

        last_message = _normalize_ts(last_message)

        if now - last_message < timedelta(hours=NUDGE_AFTER_HOURS):
            continue

        if last_nudge:
            last_nudge = _normalize_ts(last_nudge)
            if now - last_nudge < timedelta(hours=NUDGE_REPEAT_HOURS):
                continue

        try:
            await bot_inst.send_message(
                user["user_id"],
                "Ну как ты сейчас? Я на связи, если захочешь поговорить."
            )
            await update_last_nudge(user["user_id"])
        except Exception as exc:
            logger.exception("Failed to send nudge to %s: %s", user["user_id"], exc)


async def nudge_loop():
    while True:
        try:
            await send_nudges()
        except Exception as exc:
            logger.exception("Nudge loop error: %s", exc)

        await asyncio.sleep(NUDGE_CHECK_INTERVAL_SECONDS)


async def main() -> None:
    global bot

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    await connect_db()

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    nudge_task = asyncio.create_task(nudge_loop())

    try:
        dp.include_router(router)
        logger.info("Starting bot with model=%s thinking=%s", MODEL_NAME, THINKING_MODE)
        await dp.start_polling(bot)
    finally:
        nudge_task.cancel()
        with suppress(asyncio.CancelledError):
            await nudge_task
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())