import asyncio
import logging
import os
import json
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
from test_data import TEST
from keyboards import start_test_keyboard, answer_keyboard
from db import (
    connect_db,
    close_db,
    ensure_user,
    save_message,
    load_recent_messages,
    get_user,
    get_profile,
    upsert_profile,
    set_test_completed,
    get_summary,
    update_summary
)

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("deepseek-telegram-bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

SYSTEM_PROMPT = '''
    Ты — поддерживающий психологически ориентированный собеседник(мужчина). Не ставь диагнозов, не используй клинические ярлыки и не приписывай человеку расстройства.
    Твоя задача — помогать пользователю прояснять состояние, снижать напряжение и находить следующий небольшой шаг. Не критикуй пользователя, только помогай
    Используй профиль пользователя, чтобы адаптировать стиль ответа.
    Не упоминай сам профиль напрямую.
    Не пиши большого текста, если пользователь любит подобные объяснения - это не значит что надо расписать ему каждое слово
    Язык: русский.
    Используй HTML-разметку Telegram:
    - <b>жирный</b>
    - <i>курсив</i>
    - <u>подчёркнутый</u>
    НЕЛЬЗЯ использовать теги <ul>, <li>, <br>, <div>
'''


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

    summary = await get_summary(user_id)

    messages = [{"role": "system", "content": system_prompt}]

    if summary:
        messages.append({
            "role": "system",
            "content": f"Краткий контекст диалога:\n{summary}"
        })

    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    return messages


async def ask_deepseek(user_id: int, history: List[dict], user_text: str) -> LLMResult:
    messages = await build_messages(user_id, history, user_text)

    # logger.info("=== REQUEST TO DEEPSEEK ===")
    # logger.info(json.dumps(messages, ensure_ascii=False, indent=2))

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

    def level(value, low=0.3, high=0.7):
        if value is None:
            return "unknown"
        if value < low:
            return "low"
        elif value > high:
            return "high"
        return "medium"

    # --- интроверсия ---
    lvl = level(profile.get("introversion"))
    if lvl == "high":
        parts.append("Пользователь предпочитает спокойное, ненавязчивое общение.")
    elif lvl == "low":
        parts.append("Пользователь комфортно чувствует себя в активном взаимодействии.")

    # --- поддержка ---
    lvl = level(profile.get("need_support"))
    if lvl == "high":
        parts.append("Ему важны эмпатия, поддержка и бережный тон.")
    elif lvl == "low":
        parts.append("Он меньше нуждается в эмоциональной поддержке, можно говорить более нейтрально.")

    # --- прямота ---
    lvl = level(profile.get("directness"))
    if lvl == "high":
        parts.append("Предпочитает прямые и ясные формулировки.")
    elif lvl == "low":
        parts.append("Лучше использовать мягкие и осторожные формулировки.")

    # --- детализация ---
    lvl = level(profile.get("detail_preference"))
    if lvl == "high":
        parts.append("Любит структурированные и подробные объяснения.")
    elif lvl == "low":
        parts.append("Предпочитает краткие ответы без лишних деталей.")

    # --- тревожность ---
    lvl = level(profile.get("anxiety"))
    if lvl == "high":
        parts.append("Склонен к тревожности, важно снижать напряжение и давать ощущение безопасности.")
    elif lvl == "low":
        parts.append("Обычно эмоционально стабилен.")

    # --- самооценка ---
    lvl = level(profile.get("self_esteem"))
    if lvl == "low":
        parts.append("Склонен к самокритике, важно избегать давления и оценочных суждений.")
    elif lvl == "high":
        parts.append("Уверен в себе.")

    # --- чувствительность ---
    lvl = level(profile.get("emotional_sensitivity"))
    if lvl == "high":
        parts.append("Чувствителен к словам, важно быть аккуратным в формулировках.")

    # --- доверие ---
    lvl = level(profile.get("trust"))
    if lvl == "low":
        parts.append("Может быть насторожен, важно говорить прозрачно и без скрытых смыслов.")
    elif lvl == "high":
        parts.append("Склонен доверять.")

    # --- руминации ---
    lvl = level(profile.get("rumination"))
    if lvl == "high":
        parts.append("Склонен зацикливаться на мыслях, полезны техники переключения.")

    # --- контроль ---
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

async def summarize_dialogue(user_id: int, history: list[dict]) -> str:
    old_summary = await get_summary(user_id)

    messages = [
        {
            "role": "system",
            "content": (
                "Ты обновляешь долговременную память о пользователе.\n\n"
                "Правила:\n"
                "- НЕ теряй важные факты\n"
                "- Сохраняй стабильные характеристики\n"
                "- Убирай временные детали\n"
                "- Пиши кратко (3-6 предложений)\n"
                "- Верни только сам текст, без списков и без заголовков"
            )
        },
        {
            "role": "system",
            "content": f"Текущая память:\n{old_summary or 'пусто'}"
        },
        *history,
        {
            "role": "user",
            "content": "Сделай краткое обновление памяти о пользователе. Верни только готовый текст."
        }
    ]

    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        max_tokens=150,
        temperature=0.3,
    )

    return (response.choices[0].message.content or "").strip()

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not message.from_user:
        return

    await ensure_user(message.from_user.id, message.from_user.username)

    user = await get_user(message.from_user.id)
    if user and not user["test_completed"]:
        await message.answer(
            "Привет, я на связи.\n\n"
            "Для начала хочу предложить тебе тест чтобы я смог подстроить свой стиль общения. "
            "Его прохождение займет около 2 минут:",
            reply_markup=start_test_keyboard(),
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
        history = await load_recent_messages(user_id, limit=6)
        result = await ask_deepseek(user_id, history, user_text)
    except Exception as exc:
        logger.exception("DeepSeek request failed: %s", exc)
        await message.answer(
            "Сейчас не получилось получить ответ. Попробуй ещё раз через несколько секунд."
        )
        return

    reply = result.text or "Я не смог сформировать ответ. Попробуй переформулировать сообщение."

    await save_message(user_id, "user", user_text)
    await save_message(user_id, "assistant", reply)

    await message.answer(reply, parse_mode="HTML")
    history_for_summary = history + [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": reply},
    ]

    summary = await summarize_dialogue(user_id, history_for_summary)

    print("SUMMARY:", summary)

    if summary:
        await update_summary(user_id, summary)
    else:
        print("SUMMARY EMPTY, NOT SAVED")

@router.callback_query(F.data == "start_test")
async def start_test(callback, state: FSMContext):
    questions = TEST

    await state.update_data(
        questions=questions,
        answers=[],
        index=0,
    )

    await state.set_state(TestState.answering)

    total = len(questions)

    await callback.message.edit_text(
        f"Вопрос 1/{total}\n\n"
        f"{questions[0]['question']}\n\n"
        "1 — не согласен\n5 — полностью согласен",
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

        await callback.message.edit_text("Тест завершен, спасибо за уделенное время! Теперь можешь задавать вопрос")

        await callback.answer()
        return

    # следующий вопрос
    await state.update_data(index=index, answers=answers)

    total = len(questions)
    next_question = f"Вопрос {index + 1}/{total}\n\n" + questions[index]["question"]

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