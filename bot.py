import os
import re
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ["BOT_TOKEN"]
MY_TELEGRAM_ID = int(os.environ["MY_TELEGRAM_ID"])

PROCUREMENT_KEYWORDS = [
    "нужн", "заказ", "купи", "купить", "найди", "найти", "достать",
    "тарелк", "стакан", "бокал", "вилк", "ложк", "нож", "посуд",
    "оборудован", "холодильник", "духовк", "плит", "фритюр", "гриль",
    "декор", "мебель", "стул", "стол", "диван", "светильник", "лампа",
    "упаковк", "контейнер", "пакет", "салфетк", "скатерт",
    "форм", "противень", "кастрюл", "сковород", "миск",
    "шт", "штук", "штуки", "единиц", "компл", "комплект",
    "поставк", "партия", "партию", "заявк",
]

def is_procurement_request(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in PROCUREMENT_KEYWORDS)

def extract_item(text: str) -> str:
    text = re.sub(r'нужно|нужны|нужна|заказать|купить|найти|достать|пожалуйста|срочно', '', text, flags=re.IGNORECASE)
    return text.strip()[:60].strip()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat = update.message.chat
    if chat.type not in ["group", "supergroup"]:
        return

    text = update.message.text
    sender = update.message.from_user.full_name if update.message.from_user else "Неизвестный"

    urls = re.findall(r'https?://\S+', text)
    if urls:
        msg = f"🔗 *Ссылка на товар*\n\n*От:* {sender}\n*Сообщение:* _{text[:200]}_\n\n*Ссылки:*\n"
        for url in urls[:3]:
            msg += f"• {url}\n"
        msg += "\n⚠️ _Проверь цену и найди дешевле_"
        await context.bot.send_message(chat_id=MY_TELEGRAM_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
        return

    if not is_procurement_request(text):
        return

    item = extract_item(text)
    query = item.replace(' ', '+')

    msg = f"📦 *Новая заявка на закупку*\n\n"
    msg += f"*От:* {sender}\n"
    msg += f"*Запрос:* _{text[:200]}_\n\n"
    msg += f"🔍 *Где искать «{item}»:*\n\n"
    msg += f"• [Яндекс.Маркет](https://market.yandex.ru/search?text={query})\n"
    msg += f"• [Ozon](https://www.ozon.ru/search/?text={query})\n"
    msg += f"• [Wildberries](https://www.wildberries.ru/catalog/0/search.aspx?search={query})\n"
    msg += f"• [Restorus HoReCa](https://restorus.ru/search/?q={query})\n"
    msg += f"• [Первый HoReCa](https://1horeca.ru/search/?q={query})\n"

    await context.bot.send_message(chat_id=MY_TELEGRAM_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=True)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_message
    ))
    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
