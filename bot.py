import os
import re
import uuid
import asyncio
import json
import aiohttp
import ssl
from urllib.parse import quote_plus
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ["BOT_TOKEN"]
MY_TELEGRAM_ID = int(os.environ["MY_TELEGRAM_ID"])
GIGACHAT_AUTH_KEY = os.environ["GIGACHAT_AUTH_KEY"]

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


async def get_gigachat_token():
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {GIGACHAT_AUTH_KEY}",
    }
    data = "scope=GIGACHAT_API_PERS"
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            headers=headers,
            data=data,
        ) as resp:
            result = await resp.json()
            return result.get("access_token")


async def analyze_with_gigachat(text: str) -> dict | None:
    token = await get_gigachat_token()
    if not token:
        return None

    prompt = f"""Проанализируй сообщение и определи является ли это заявкой на закупку товаров для ресторана/кафе.

Сообщение: "{text}"

Если ДА - заявка на закупку, верни ТОЛЬКО JSON в одну строку без переносов:
{{"is_request": true, "item": "название товара", "quantity": "количество", "color": "цвет", "material": "материал", "notes": "дополнительные требования"}}

Если поля нет в сообщении - оставляй пустую строку "".

Если это НЕ заявка на закупку (обычное общение, шутка, вопрос), верни:
{{"is_request": false}}

ВАЖНО: верни только JSON, без markdown, без объяснений."""

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    payload = {
        "model": "GigaChat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 300,
    }

    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            result = await resp.json()

    try:
        content = result["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        content = content.strip()
        data = json.loads(content)
        return data if data.get("is_request") else None
    except Exception as e:
        print(f"Parse error: {e}, response: {result}")
        return None


def build_search_links(item: str) -> str:
    """Build search URLs - marketplaces direct, HoReCa via Yandex site search."""
    q = quote_plus(item)
    yq = quote_plus(item)  # для Yandex site search

    # Маркетплейсы — прямые ссылки, проверены, работают
    marketplaces = (
        f"🛒 *Маркетплейсы:*\n"
        f"• [Яндекс.Маркет](https://market.yandex.ru/search?text={q})\n"
        f"• [Ozon](https://www.ozon.ru/search/?text={q})\n"
        f"• [Wildberries](https://www.wildberries.ru/catalog/0/search.aspx?search={q})\n"
        f"• [ВсеИнструменты](https://www.vseinstrumenti.ru/search/?what={q})\n"
    )

    # HoReCa поставщики — через Яндекс с привязкой к сайту (надёжно)
    horeca = (
        f"\n🍽 *HoReCa поставщики:*\n"
        f"• [Комплекс Бар](https://yandex.ru/search/?text={yq}+complexbar.ru)\n"
        f"• [Барнео](https://yandex.ru/search/?text={yq}+barneo.ru)\n"
        f"• [Ресторан Комплект](https://yandex.ru/search/?text={yq}+r-komplekt.ru)\n"
        f"• [РестИнтернэшнл](https://yandex.ru/search/?text={yq}+restinternational.ru)\n"
    )

    return marketplaces + horeca


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat = update.message.chat
    if chat.type not in ["group", "supergroup"]:
        return

    text = update.message.text
    sender = update.message.from_user.full_name if update.message.from_user else "Неизвестный"

    urls = re.findall(r"https?://\S+", text)
    if urls:
        msg = f"🔗 *Ссылка на товар*\n\n"
        msg += f"*От:* {sender}\n"
        msg += f"*Сообщение:* _{text[:200]}_\n\n"
        msg += "*Ссылки:*\n"
        for url in urls[:3]:
            msg += f"• {url}\n"
        msg += "\n⚠️ _Проверь цену и найди дешевле_"
        await context.bot.send_message(
            chat_id=MY_TELEGRAM_ID,
            text=msg,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        return

    try:
        analysis = await analyze_with_gigachat(text)
    except Exception as e:
        print(f"GigaChat error: {e}")
        return

    if not analysis:
        return

    item = analysis.get("item", "товар").strip()
    quantity = analysis.get("quantity", "").strip()
    color = analysis.get("color", "").strip()
    material = analysis.get("material", "").strip()
    notes = analysis.get("notes", "").strip()

    search_query_parts = [item]
    if color:
        search_query_parts.append(color)
    if material:
        search_query_parts.append(material)
    search_query = " ".join(search_query_parts)

    msg = f"📦 *Новая заявка на закупку*\n\n"
    msg += f"👤 *От:* {sender}\n"
    msg += f"📝 *Исходный запрос:* _{text[:200]}_\n\n"
    msg += f"━━━━━━━━━━━━━━\n"
    msg += f"🛒 *Что нужно:* {item}\n"
    if quantity:
        msg += f"🔢 *Количество:* {quantity}\n"
    if color:
        msg += f"🎨 *Цвет:* {color}\n"
    if material:
        msg += f"🧱 *Материал:* {material}\n"
    if notes:
        msg += f"📌 *Примечания:* {notes}\n"
    msg += f"━━━━━━━━━━━━━━\n\n"
    msg += f"🔍 *Найти цены:*\n\n"
    msg += build_search_links(search_query)
    msg += "\n💡 _Сравни цены и выбери лучшее предложение_"

    await context.bot.send_message(
        chat_id=MY_TELEGRAM_ID,
        text=msg,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            handle_message,
        )
    )
    print("Бот запущен с GigaChat...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
