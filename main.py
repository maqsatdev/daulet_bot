import asyncio
import aiohttp
import requests
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

BOT_TOKEN = "8706646874:AAEsrTRcCfWB9ogKXggUVfgCH-zVRHLf_7w"

BASE_URL = "https://n551098.alteg.io/api/v1/booking/search"
HEADERS = {
    "Authorization": "Bearer gtcwf654agufy25gsadh",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://n551098.alteg.io",
    "Referer": "https://n551098.alteg.io/company/521176/personal/select-master?o=",
    "X-Altegio-Application-Name": "client.booking",
}

COURTS = {
    1513587: "Корт 1 (крытый)",
    1521552: "Корт 2 (крытый)",
    1521553: "Корт 3 (крытый)",
    1521555: "Корт 4 (крытый)",
    1521557: "Корт 5 (крытый)",
    1521558: "Корт 6 (крытый)",
    1521559: "Корт 7 (крытый)",
    1521561: "Корт 8 (крытый)",
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ADMIN_CHAT_ID = None
# Ключ словаря теперь кортеж (staff_id, date_str), чтобы хранить слоты по дням по отдельности
previous_slots_state = {}

def get_target_dates():
    """Возвращает список из 3 дат: сегодня, завтра, послезавтра в формате YYYY-MM-DD"""
    today = datetime.now()
    return [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]

@dp.message(Command('start'))
async def handle_start(message: Message):
    global ADMIN_CHAT_ID
    ADMIN_CHAT_ID = message.chat.id

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎾 Посмотреть свободные корты (на 3 дня)",
                    callback_data="show_free_courts"
                )
            ]
        ]
    )
    await message.answer(
        "Привет, Макс! Бот запущен и следит за свободными кортами на 3 дня вперед.\n"
        "Нажмите кнопку ниже, чтобы проверить расписание:",
        reply_markup=keyboard
    )

# Ручной вывод сводки по всем кортам на 3 дня
@dp.callback_query(F.data == "show_free_courts")
async def process_show_courts(callback: CallbackQuery):
    await callback.answer()

    status_msg = await callback.message.answer("⏳ Проверяю корты на 3 дня вперед, подождите...")

    dates = get_target_dates()
    connector = aiohttp.TCPConnector(ssl=False)

    report_lines = ["<b>📊 Свободные корты на ближайшие 3 дня:</b>\n"]

    async with aiohttp.ClientSession(connector=connector) as session:
        for date_str in dates:
            report_lines.append(f"📅 <b>Дата: {date_str}</b>")
            has_slots_on_date = False

            for staff_id, court_name in COURTS.items():
                payload = {
                    "context": {"location_id": 521176},
                    "filter": {
                        "date": date_str,
                        "records": [{"staff_id": staff_id, "attendance_service_items": []}]
                    }
                }

                try:
                    async with session.post(f"{BASE_URL}/timeslots", headers=HEADERS, json=payload) as response:
                        if response.status == 200:
                            data = await response.json()
                            timeslots = data.get("data", [])

                            free_times = []
                            for slot in timeslots:
                                attrs = slot.get("attributes", {})
                                if attrs.get("is_bookable", True):
                                    time_str = attrs.get("time")
                                    if time_str:
                                        free_times.append(time_str)

                            if free_times:
                                times_str = ", ".join(free_times)
                                report_lines.append(f"  • {court_name}: 🟢 {times_str}")
                                has_slots_on_date = True
                except Exception:
                    pass

                await asyncio.sleep(0.1) # Короткая пауза между запросами

            if not has_slots_on_date:
                report_lines.append("  • <i>Свободных мест нет ни на одном корте</i>")
            report_lines.append("") # Пустая строка для читаемости

    await status_msg.delete()

    # В телеграм сообщения имеют лимит по символам, но на 3 дня должно хватить. Отправляем результат:
    await callback.message.answer("\n".join(report_lines), parse_mode="HTML")


# Фоновая задача мониторинга на 3 дня вперед
async def background_court_monitor():
    await asyncio.sleep(15)

    while True:
        try:
            if ADMIN_CHAT_ID:
                dates = get_target_dates()

                # Используем обычный requests внутри потока, чтобы избежать любых проблем с сессиями aiohttp
                def fetch_timeslots(staff_id, date_str):
                    payload = {
                        "context": {"location_id": 521176},
                        "filter": {
                            "date": date_str,
                            "records": [{"staff_id": staff_id, "attendance_service_items": []}]
                        }
                    }
                    # Отключаем предупреждения о неверном SSL для requests через verify=False
                    response = requests.post(f"{BASE_URL}/timeslots", headers=HEADERS, json=payload, timeout=10, verify=False)
                    if response.status_code == 200:
                        return response.json()
                    return None

                for date_str in dates:
                    for staff_id, court_name in COURTS.items():
                        try:
                            # Запускаем синхронный запрос в отдельном потоке, чтобы не блокировать бота
                            data = await asyncio.to_thread(fetch_timeslots, staff_id, date_str)

                            if data:
                                timeslots = data.get("data", [])
                                current_free_times = set()
                                for slot in timeslots:
                                    attrs = slot.get("attributes", {})
                                    if attrs.get("is_bookable", True):
                                        time_str = attrs.get("time")
                                        if time_str:
                                            current_free_times.add(time_str)

                                state_key = (staff_id, date_str)
                                previous_free_times = previous_slots_state.get(state_key, set())

                                new_slots = current_free_times - previous_free_times

                                if new_slots and previous_free_times:
                                    formatted_new = ", ".join(sorted(new_slots))
                                    text = (
                                        f"🚨 <b>Появилось новое свободное время!</b>\n\n"
                                        f"🎾 <b>{court_name}</b>\n"
                                        f"📅 Дата: {date_str}\n"
                                        f"⏰ Время: {formatted_new}"
                                    )
                                    await bot.send_message(ADMIN_CHAT_ID, text, parse_mode="HTML")

                                previous_slots_state[state_key] = current_free_times

                        except Exception as inner_e:
                            print(f"Ошибка запроса корта {staff_id} на {date_str}: {inner_e}")

                        await asyncio.sleep(0.5)

        except Exception as e:
            print(f"Ошибка в фоне: {e}")

        # Пауза перед следующей глобальной проверкой всех кортов (3 минуты)
        await asyncio.sleep(180)

async def main():
    asyncio.create_task(background_court_monitor())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())