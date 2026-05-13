import os
import re
import json
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic

BOT_TOKEN = os.environ["BOT_TOKEN"]
MY_TELEGRAM_ID = int(os.environ["MY_TELEGRAM_ID"])
GROUP_NAME = os.environ.get("GROUP_NAME", "")

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


async def is_procurement_request(text: str) -> dict | None:
    """Use Claude to detect if message is a procurement request."""
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""Определи является ли это сообщение заявкой на закупку товаров для ресторана/кафе.

Сообщение: {text}

Если да — верни JSON:
{{"is_request": true, "item": "название товара", "quantity": "количество если указано", "notes": "доп. требования"}}

Если нет — верни:
{{"is_request": false}}

Только JSON, никакого текста."""
        }]
    )
    
    try:
        result = json.loads(response.content[0].text.strip())
        return result if result.get("is_request") else None
    except:
        return None


async def search_prices(item: str, session: aiohttp.ClientSession) -> list[dict]:
    """Search for prices via Yandex Shopping."""
    results = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    search_url = f"https://yandex.ru/search/?text={item}+купить+цена&lr=213"
    
    try:
        async with session.get(search_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                html = await resp.text()
                soup = BeautifulSoup(html, "html.parser")
                
                for result in soup.select(".serp-item")[:5]:
                    title_el = result.select_one("h2 a, .organic__title a")
                    if title_el:
                        title = title_el.get_text(strip=True)
                        link = title_el.get("href", "")
                        if link and not link.startswith("http"):
                            link = "https://yandex.ru" + link
                        
                        price_el = result.select_one(".price, [class*='price']")
                        price = price_el.get_text(strip=True) if price_el else "цена не указана"
                        
                        if title and link:
                            results.append({
                                "title": title[:80],
                                "price": price,
                                "link": link
                            })
    except Exception as e:
        print(f"Search error: {e}")
    
    # Fallback: market search
    if not results:
        market_url = f"https://market.yandex.ru/search?text={item}"
        results.append({
            "title": f"Поиск на Яндекс.Маркет",
            "price": "открыть для просмотра цен",
            "link": market_url
        })
    
    return results


async def format_result(request_info: dict, prices: list[dict], original_text: str, sender: str) -> str:
    """Format the result message."""
    item = request_info.get("item", "неизвестный товар")
    quantity = request_info.get("quantity", "не указано")
    notes = request_info.get("notes", "")
    
    msg = f"📦 *Новая заявка на закупку*\n\n"
    msg += f"*От:* {sender}\n"
    msg += f"*Товар:* {item}\n"
    msg += f"*Количество:* {quantity}\n"
    if notes:
        msg += f"*Примечания:* {notes}\n"
    msg += f"\n*Исходное сообщение:*\n_{original_text[:200]}_\n\n"
    
    if prices:
        msg += "🔍 *Найденные варианты:*\n\n"
        for i, p in enumerate(prices[:5], 1):
            msg += f"{i}. {p['title']}\n"
            msg += f"   💰 {p['price']}\n"
            msg += f"   🔗 {p['link']}\n\n"
    else:
        msg += "❌ Цены не найдены автоматически — проверь вручную.\n"
    
    return msg


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming group messages."""
    if not update.message or not update.message.text:
        return
    
    chat = update.message.chat
    text = update.message.text
    sender = update.message.from_user.full_name if update.message.from_user else "Неизвестный"
    
    # Only process group/supergroup messages
    if chat.type not in ["group", "supergroup"]:
        return
    
    # Check if it's a procurement request
    request_info = await is_procurement_request(text)
    if not request_info:
        return
    
    # Search for prices
    async with aiohttp.ClientSession() as session:
        prices = await search_prices(request_info["item"], session)
    
    # Format and send to me
    result = await format_result(request_info, prices, text, sender)
    
    await context.bot.send_message(
        chat_id=MY_TELEGRAM_ID,
        text=result,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages with links."""
    if not update.message or not update.message.text:
        return
    
    chat = update.message.chat
    if chat.type not in ["group", "supergroup"]:
        return
    
    text = update.message.text
    urls = re.findall(r'https?://\S+', text)
    
    if not urls:
        return
    
    sender = update.message.from_user.full_name if update.message.from_user else "Неизвестный"
    
    msg = f"🔗 *Ссылка на товар*\n\n"
    msg += f"*От:* {sender}\n"
    msg += f"*Исходное сообщение:*\n_{text[:300]}_\n\n"
    msg += f"*Ссылки:*\n"
    for url in urls[:3]:
        msg += f"• {url}\n"
    
    msg += "\n⚠️ _Проверь цену по ссылке и найди альтернативы._"
    
    await context.bot.send_message(
        chat_id=MY_TELEGRAM_ID,
        text=msg,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Handle messages with links
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Entity("url") & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_link
    ))
    
    # Handle regular text messages
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        handle_message
    ))
    
    print("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
