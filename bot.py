import json
import datetime
import os
import re
from difflib import SequenceMatcher
from typing import Tuple

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv
from aiogram.filters import StateFilter
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Awaitable, Dict, Any, Union
from time import monotonic

class AntiFloodMiddleware(BaseMiddleware):
    """
    Рейт-лимит на пользователя:
    - limit_msg: минимальный интервал между сообщениями пользователя (сек)
    - limit_cb:  минимальный интервал между нажатиями кнопок (сек)
    Сообщения/клики, пришедшие раньше интервала, просто тихо игнорируются.
    """
    def __init__(self, limit_msg: float = 0.8, limit_cb: float = 0.3):
        self.limit_msg = limit_msg
        self.limit_cb = limit_cb
        self._last_msg: Dict[int, float] = {}
        self._last_cb: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        now = monotonic()

        # Ограничение по входящим сообщениям
        if isinstance(event, Message) and event.from_user:
            uid = event.from_user.id
            last = self._last_msg.get(uid, 0.0)
            if (now - last) < self.limit_msg:
                return  # тихо отбрасываем
            self._last_msg[uid] = now

        # Ограничение по нажатиям кнопок
        if isinstance(event, CallbackQuery) and event.from_user:
            uid = event.from_user.id
            last = self._last_cb.get(uid, 0.0)
            if (now - last) < self.limit_cb:
                # Можно ответить "молча", чтобы Telegram убрал "часики"
                try:
                    await event.answer()
                except Exception:
                    pass
                return
            self._last_cb[uid] = now

        return await handler(event, data)



# ====== Config ======
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")          # кому отправлять заявки
SUPPORT_CHAT_ID = int(os.getenv("SUPPORT_CHAT_ID", 0))  # группа поддержки

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN не найден. Укажите токен в .env")

# ====== Логирование ======
LOG_FILE = "chat_logs.txt"

def log_message(user_id, username, role, text):
    """Записывает строку в файл лога."""
    time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uname = f"@{username}" if username else ""
    clean_text = text.replace("\n", "\\n") if text else "<не текст>"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{time}] ({user_id}) {uname} [{role}]: {clean_text}\n")

async def log_and_forward_incoming(message: Message, bot: Bot):
    """Логируем входящее и пересылаем в группу поддержки (если задана)."""
    log_message(message.from_user.id, message.from_user.username, "USER", message.text or "<не текст>")
    if SUPPORT_CHAT_ID:
        try:
            await bot.forward_message(
                chat_id=SUPPORT_CHAT_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
        except Exception as e:
            print("Не удалось отправить в группу поддержки:", e)

async def send_and_log(message: Message, text: str, **kwargs):
    """Отправляет сообщение и логирует как ответ бота."""
    log_message(message.from_user.id, message.from_user.username, "BOT", text)
    return await message.answer(text, **kwargs)



# ====== Data ======
FAQ = {}
CATEGORIES = []
QUICK_REPLIES = []

with open("faq.json", "r", encoding="utf-8") as f:
    data = json.load(f)
    FAQ = data
    CATEGORIES = data.get("categories", [])
    QUICK_REPLIES = data.get("quick_replies", [])

# ====== Utils ======
def build_main_kb():
    kb = InlineKeyboardBuilder()
    for item in QUICK_REPLIES:
        kb.button(text=item["text"], callback_data=item["callback"])
    kb.adjust(1)
    return kb.as_markup()

def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[\s]+", " ", text)
    text = re.sub(r"[.,!?()\-:;]", "", text)
    return text

def best_faq_answer(user_text: str, threshold: float = 0.63) -> Tuple[dict, float]:
    q = normalize(user_text)
    best_score = 0.0
    best_item = {}
    for cat in CATEGORIES:
        for item in cat.get("items", []):
            for pattern in item.get("patterns", []):
                score = SequenceMatcher(None, q, normalize(pattern)).ratio()
                if score > best_score:
                    best_score = score
                    best_item = item
    if best_score >= threshold:
        return best_item, best_score
    return {}, best_score


# ====== FSM ======
class Apply(StatesGroup):
    name = State()
    age = State()
    contact = State()
    experience = State()

# ====== Router ======
router = Router()

# ====== Команды ======
@router.message(CommandStart())
async def on_start(message: Message, bot: Bot):
    await log_and_forward_incoming(message, bot)
    text = (
        "Привет! Я помощник по стримингу Bigo Live. Помогу подать заявку и отвечу на вопросы.\n\n"
        "Выберите действие ниже или напишите вопрос."
    )
    await send_and_log(message, text, reply_markup=build_main_kb())

@router.message(Command("help"))
async def on_help(message: Message, bot: Bot):
    await log_and_forward_incoming(message, bot)
    await send_and_log(message, "Напишите ваш вопрос словами — я постараюсь ответить. Команды: /apply, /faq, /contact")

@router.message(Command("faq"))
async def on_faq(message: Message, bot: Bot):
    await log_and_forward_incoming(message, bot)
    kb = InlineKeyboardBuilder()
    for idx, cat in enumerate(CATEGORIES):
        kb.button(text=cat.get("title", f"Категория {idx+1}"), callback_data=f"cat:{idx}")
    kb.adjust(1)
    await send_and_log(message, "Выберите категорию:", reply_markup=kb.as_markup())

from aiogram import Bot  # если не импортирован выше

@router.callback_query(F.data == "faq")
async def cb_faq(callback: CallbackQuery, bot: Bot):
    await on_faq(callback.message, bot)
    await callback.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery):
    idx = int(callback.data.split(":")[1])
    cat = CATEGORIES[idx]
    kb = InlineKeyboardBuilder()
    for i, item in enumerate(cat.get("items", [])):
        first_pattern = item.get("patterns", ["Вопрос"])[0]
        kb.button(text=first_pattern.capitalize(), callback_data=f"item:{idx}:{i}")
    kb.button(text="← Назад", callback_data="faq")
    kb.adjust(1)
    # здесь callback.message — это сообщение бота; логировать исходящее:
    await send_and_log(callback.message, f"Категория: {cat.get('title')}")
    await callback.message.edit_text(f"Категория: {cat.get('title')}", reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("item:"))
async def cb_item(callback: CallbackQuery):
    _, cidx, iidx = callback.data.split(":")
    cidx, iidx = int(cidx), int(iidx)
    item = CATEGORIES[cidx]["items"][iidx]
    await send_and_log(callback.message, item.get("answer", ""), reply_markup=build_main_kb())
    await callback.answer()

# ====== Анкета ======
@router.message(Command("apply"))
@router.callback_query(F.data == "apply")
async def start_apply(event, state: FSMContext, bot: Bot):
    message = event if isinstance(event, Message) else event.message
    await log_and_forward_incoming(message, bot)
    await send_and_log(message, "Давайте подадим заявку! Как вас зовут? (Имя и фамилия)")
    await state.set_state(Apply.name)
    if not isinstance(event, Message):
        await event.answer()

@router.message(Apply.name)
async def apply_name(message: Message, state: FSMContext, bot: Bot):
    await log_and_forward_incoming(message, bot)
    await state.update_data(name=message.text.strip())
    await send_and_log(message, "Сколько вам лет?")
    await state.set_state(Apply.age)

@router.message(Apply.age)
async def apply_age(message: Message, state: FSMContext, bot: Bot):
    await log_and_forward_incoming(message, bot)
    age_text = message.text.strip()
    if not age_text.isdigit():
        await send_and_log(message, "Пожалуйста, укажите возраст цифрами.")
        return
    age = int(age_text)
    if age < 18:
        await send_and_log(message, "К сожалению, участвовать в программе могут только пользователи 18+. Спасибо за интерес к платформе!")
        await state.clear()
        return
    await state.update_data(age=age)
    await send_and_log(message, "Оставьте контакт: @username или номер телефона")
    await state.set_state(Apply.contact)

@router.message(Apply.contact)
async def apply_contact(message: Message, state: FSMContext, bot: Bot):
    await log_and_forward_incoming(message, bot)
    await state.update_data(contact=message.text.strip())
    await send_and_log(message, "Есть ли опыт стриминга? Коротко опишите (или напишите \"нет\").")
    await state.set_state(Apply.experience)

@router.message(Apply.experience)
async def apply_done(message: Message, state: FSMContext, bot: Bot):
    await log_and_forward_incoming(message, bot)
    await state.update_data(experience=message.text.strip())
    data = await state.get_data()
    await state.clear()
    await send_and_log(message, "Спасибо! Заявка отправлена менеджеру. Мы свяжемся в ближайшее время.", reply_markup=build_main_kb())
    summary = (
        "📝 Новая заявка на стримера\n"
        f"Имя: {data.get('name')}\n"
        f"Возраст: {data.get('age')}\n"
        f"Контакт: {data.get('contact')}\n"
        f"Опыт: {data.get('experience')}"
    )
    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=summary)
        except Exception as e:
            print("Не удалось отправить менеджеру:", e)
    else:
        print("ADMIN_CHAT_ID не задан. Заявка:\n" + summary)

# ====== Контакт менеджера ======
@router.message(Command("contact"))
@router.callback_query(F.data == "contact")
async def contact(event, bot: Bot):
    message = event if isinstance(event, Message) else event.message
    await log_and_forward_incoming(message, bot)
    text = (
        "Для прохождения кастинга или если остался вопрос — напишите менеджеру.\n"
        "Ватсап: +79183253080."
    )
    await send_and_log(message, text, reply_markup=build_main_kb())
    if not isinstance(event, Message):
        await event.answer()

# ====== Свободный текст (FAQ) ======
@router.message(StateFilter(None))
async def on_free_text(message: Message, bot: Bot):
    await log_and_forward_incoming(message, bot)
    item, score = best_faq_answer(message.text or "")
    if item:
        answer = item.get("answer", "")
        image = item.get("image")

        if image:  # если в JSON есть картинка
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=image,
                caption=answer,
                reply_markup=build_main_kb()
            )
            log_message(message.from_user.id, message.from_user.username, "BOT", f"[PHOTO] {answer}")
        else:
            await send_and_log(message, answer, reply_markup=build_main_kb())
    else:
        await send_and_log(
            message,
            "Пока не нашёл точный ответ. Выберите, что вам нужно:",
            reply_markup=build_main_kb()
        )



# ====== App bootstrap ======
async def main():
    dp = Dispatcher(storage=MemoryStorage())

    # ── антифлуд: один экземпляр на сообщения и коллбэки ──
    af = AntiFloodMiddleware(limit_msg=0.8, limit_cb=0.3)
    dp.message.middleware(af)
    dp.callback_query.middleware(af)

    dp.include_router(router)
    bot = Bot(BOT_TOKEN)
    print("🤖 Bot is running. Press Ctrl+C to stop.")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped")
