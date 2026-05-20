import os
import re
import uuid
import asyncio
import json
import aiohttp
import ssl
from urllib.parse import quote_plus, urlparse
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ["BOT_TOKEN"]
MY_TELEGRAM_ID = int(os.environ["MY_TELEGRAM_ID"])
GIGACHAT_AUTH_KEY = os.environ["GIGACHAT_AUTH_KEY"]

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


# ─────────────────────────────────────
# GigaChat для распознавания заявок
# ─────────────────────────────────────
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
            headers=headers, data=data,
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
            headers=headers, json=payload,
        ) as resp:
            result = await resp.json()
    try:
        content = result["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        data = json.loads(content.strip())
        return data if data.get("is_request") else None
    except Exception as e:
        print(f"Parse error: {e}, response: {result}")
        return None


# ─────────────────────────────────────
# Wildberries — парсер карточки и поиск
# ─────────────────────────────────────
def extract_wb_id(url: str) -> str | None:
    m = re.search(r"/catalog/(\d+)/", url)
    return m.group(1) if m else None


async def get_wb_product(url: str, session: aiohttp.ClientSession) -> dict | None:
    """Получаем инфу о товаре по ссылке WB через их публичный API."""
    wb_id = extract_wb_id(url)
    if not wb_id:
        return None
    api_url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={wb_id}"
    try:
        async with session.get(api_url, headers={"User-Agent": USER_AGENT}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
        product = data["data"]["products"][0]
        return {
            "name": product.get("name", ""),
            "brand": product.get("brand", ""),
            "price": product.get("sizes", [{}])[0].get("price", {}).get("product", 0) / 100,
            "id": wb_id,
            "url": f"https://www.wildberries.ru/catalog/{wb_id}/detail.aspx",
        }
    except Exception as e:
        print(f"WB product error: {e}")
        return None


async def search_wb(query: str, session: aiohttp.ClientSession, limit: int = 5) -> list[dict]:
    q = quote_plus(query)
    api_url = f"https://search.wb.ru/exactmatch/ru/common/v5/search?appType=1&curr=rub&dest=-1257786&query={q}&resultset=catalog&sort=priceup&suppressSpellcheck=false"
    try:
        async with session.get(api_url, headers={"User-Agent": USER_AGENT}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
        products = data.get("data", {}).get("products", [])[:limit]
        result = []
        for p in products:
            price = p.get("sizes", [{}])[0].get("price", {}).get("product", 0) / 100
            result.append({
                "name": p.get("name", "")[:60],
                "brand": p.get("brand", ""),
                "price": price,
                "url": f"https://www.wildberries.ru/catalog/{p['id']}/detail.aspx",
            })
        return result
    except Exception as e:
        print(f"WB search error: {e}")
        return []


# ─────────────────────────────────────
# Ozon — поиск через публичный API
# ─────────────────────────────────────
async def search_ozon(query: str, session: aiohttp.ClientSession, limit: int = 5) -> list[dict]:
    """Возвращаем ссылку на поиск Ozon — прямой парсинг блокируется."""
    q = quote_plus(query)
    # Озон активно блокирует прямой парсинг, поэтому возвращаем ссылку на поиск с сортировкой по цене
    return [{
        "name": f"Поиск «{query}» на Озон",
        "brand": "",
        "price": 0,
        "url": f"https://www.ozon.ru/search/?text={q}&sorting=price",
    }]


def detect_marketplace(url: str) -> str | None:
    if "wildberries" in url or "wb.ru" in url:
        return "wb"
    if "ozon.ru" in url:
        return "ozon"
    return None


# ─────────────────────────────────────
# Формирование ссылок поиска (для текстовых заявок)
# ─────────────────────────────────────
def build_search_links(item: str) -> str:
    q = quote_plus(item)
    marketplaces = (
        f"🛒 *Маркетплейсы:*\n"
        f"• [Яндекс.Маркет](https://market.yandex.ru/search?text={q})\n"
        f"• [Ozon](https://www.ozon.ru/search/?text={q}&sorting=price)\n"
        f"• [Wildberries](https://www.wildberries.ru/catalog/0/search.aspx?search={q}&sort=priceup)\n"
        f"• [ВсеИнструменты](https://www.vseinstrumenti.ru/search/?what={q})\n"
    )
    horeca = (
        f"\n🍽 *HoReCa поставщики:*\n"
        f"• [Комплекс Бар](https://complexbar.ru/?dispatch=products.search&q={q})\n"
        f"• [Барнео](https://www.barneo.ru/product-search?searchText={q})\n"
        f"• [Ресторан Комплект](https://r-komplekt.ru/search/?q={q}&send=Y)\n"
        f"• [РестИнтернэшнл](https://restinternational.ru/catalog/?q={q})\n"
    )
    return marketplaces + horeca


# ─────────────────────────────────────
# Главный обработчик сообщений
# ─────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat = update.message.chat
    if chat.type not in ["group", "supergroup"]:
        return

    text = update.message.text
    sender = update.message.from_user.full_name if update.message.from_user else "Неизвестный"

    urls = re.findall(r"https?://\S+", text)
    
    # ─── Если есть ссылка на ВБ или Ozon — сравниваем цены ───
    if urls:
        product_url = urls[0]
        marketplace = detect_marketplace(product_url)
        
        if marketplace == "wb":
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                product = await get_wb_product(product_url, session)
                
                if not product:
                    await context.bot.send_message(
                        chat_id=MY_TELEGRAM_ID,
                        text=f"❌ Не удалось получить данные товара с ВБ\n\n*От:* {sender}\n*Ссылка:* {product_url}",
                        parse_mode="Markdown", disable_web_page_preview=True,
                    )
                    return
                
                # Параллельно ищем на ВБ и Ozon
                search_query = product["name"][:50]
                wb_results, ozon_results = await asyncio.gather(
                    search_wb(search_query, session),
                    search_ozon(search_query, session),
                )
            
            # Формируем ответ
            msg = f"🛍 *Сравнение цен по ссылке*\n\n"
            msg += f"👤 *От:* {sender}\n\n"
            msg += f"━━━━━━━━━━━━━━\n"
            msg += f"📦 *Исходный товар (ВБ):*\n"
            msg += f"• {product['name']}\n"
            msg += f"• Бренд: {product['brand']}\n"
            msg += f"• 💰 *{product['price']:.0f} ₽*\n"
            msg += f"• [Открыть товар]({product['url']})\n"
            msg += f"━━━━━━━━━━━━━━\n\n"
            
            # Wildberries — варианты дешевле
            cheaper_wb = [p for p in wb_results if p["price"] > 0 and p["price"] < product["price"]]
            if cheaper_wb:
                msg += f"💚 *Дешевле на ВБ:*\n"
                for p in cheaper_wb[:5]:
                    msg += f"• [{p['name']}]({p['url']}) — *{p['price']:.0f} ₽*\n"
                msg += "\n"
            else:
                if wb_results:
                    msg += f"🟢 *Похожие на ВБ:*\n"
                    for p in wb_results[:3]:
                        msg += f"• [{p['name']}]({p['url']}) — *{p['price']:.0f} ₽*\n"
                    msg += "\n"
            
            # Ozon
            msg += f"🔵 *Проверить на Ozon:*\n"
            for p in ozon_results:
                msg += f"• [{p['name']}]({p['url']})\n"
            
            await context.bot.send_message(
                chat_id=MY_TELEGRAM_ID, text=msg,
                parse_mode="Markdown", disable_web_page_preview=True,
            )
            return
        
        elif marketplace == "ozon":
            # Для Ozon просто возвращаем поиск с похожими товарами
            msg = f"🔗 *Ссылка на товар Ozon*\n\n"
            msg += f"👤 *От:* {sender}\n"
            msg += f"📝 *Сообщение:* _{text[:200]}_\n\n"
            msg += f"⚠️ Озон активно защищается от автоматического парсинга — открой товар по ссылке и сравни вручную\n\n"
            msg += f"• [Исходный товар]({product_url})\n\n"
            msg += f"🔎 *Поиск похожих:*\n"
            msg += f"• [На Ozon (дешевые сначала)](https://www.ozon.ru/search/?text=&sorting=price)\n"
            msg += f"• Также проверь Wildberries и Яндекс.Маркет\n"
            await context.bot.send_message(
                chat_id=MY_TELEGRAM_ID, text=msg,
                parse_mode="Markdown", disable_web_page_preview=True,
            )
            return
        
        else:
            # Просто пересылаем ссылку
            msg = f"🔗 *Ссылка на товар*\n\n"
            msg += f"*От:* {sender}\n*Сообщение:* _{text[:200]}_\n\n"
            msg += "*Ссылки:*\n"
            for url in urls[:3]:
                msg += f"• {url}\n"
            msg += "\n⚠️ _Проверь цену и найди дешевле_"
            await context.bot.send_message(
                chat_id=MY_TELEGRAM_ID, text=msg,
                parse_mode="Markdown", disable_web_page_preview=True,
            )
            return

    # ─── Если ссылок нет — анализируем текст через GigaChat ───
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
        chat_id=MY_TELEGRAM_ID, text=msg,
        parse_mode="Markdown", disable_web_page_preview=True,
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
            handle_message,
        )
    )
    print("Бот запущен с парсером ВБ...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
