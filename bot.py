import os
import logging
import asyncio
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler,
    filters, ContextTypes, ConversationHandler
)
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Конфиг ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ADMIN_ID         = int(os.getenv("ADMIN_ID", "0"))
TON_WALLET       = os.getenv("TON_WALLET", "ТВОЙ_TON_АДРЕС")
USDT_WALLET      = os.getenv("USDT_WALLET", "ТВОЙ_USDT_TRC20_АДРЕС")
# Для оплаты картой через Telegram Payments нужен токен от @BotFather → Payments
# Подключи провайдера (Stripe TEST: "PROVIDER_TOKEN_TEST") в BotFather → Bot Settings → Payments
CARD_PROVIDER_TOKEN = os.getenv("CARD_PROVIDER_TOKEN", "")

anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Продукты и цены ──────────────────────────────────────────────────────────
PRODUCTS = {
    "natal": {
        "name":   "🔮 Натальная карта",
        "stars":  200,          # Telegram Stars  (~$20)
        "price_usd": 20,        # для карты и крипты
        "ton":    120,          # TON
        "usdt":   20,           # USDT
        "desc":   "Полный разбор твоей натальной карты — планеты, дома, аспекты. Кто ты есть на самом деле.",
    },
    "forecast_3m": {
        "name":   "🌙 Прогноз на 3 месяца",
        "stars":  350,
        "price_usd": 30,
        "ton":    180,
        "usdt":   30,
        "desc":   "Что тебя ждёт в ближайшие три месяца — в отношениях, деньгах, здоровье и карьере.",
    },
    "forecast_year": {
        "name":   "✨ Прогноз на год",
        "stars":  550,
        "price_usd": 50,
        "ton":    300,
        "usdt":   50,
        "desc":   "Полный годовой прогноз. Ключевые периоды, возможности и предупреждения на каждый месяц.",
    },
}

# ─── Состояния ────────────────────────────────────────────────────────────────
(
    CHOOSING_PRODUCT,
    ASKING_NAME,
    ASKING_BIRTHDATE,
    ASKING_BIRTHTIME,
    ASKING_BIRTHCITY,
    ASKING_EMAIL,
    ASKING_QUESTION,
    CHOOSING_PAYMENT,
    WAITING_CRYPTO_PROOF,
) = range(9)

# ─── Системный промпт ─────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Ты — Мирра, мудрая провидица с глубокими знаниями астрологии. 
Ты говоришь спокойно, образно, с паузами в тексте (используй многоточия...). 
Ты никогда не говоришь банальностей. Каждое слово весомо.

Твой стиль: тёплый, но не слащавый. Мудрый, но не высокомерный. 
Ты видишь суть человека через его карту. Используй астрологические термины 
естественно, как часть речи. Пиши на русском языке.

Структура ответа для натальной карты:
- Вступление (2-3 предложения — обращение по имени, первое впечатление от карты)
- Солнечный знак и его проявление в этом конкретном человеке
- Луна и эмоциональный мир
- Восходящий знак (если известно время рождения)
- Ключевые планеты и аспекты
- Главное послание карты (итог, 3-4 предложения)

Структура для прогноза на 3 месяца:
- Вступление
- Месяц 1: ключевые энергии и события
- Месяц 2: ключевые энергии и события
- Месяц 3: ключевые энергии и события
- Итог и совет

Структура для прогноза на год:
- Вступление
- Общая тема года для этого человека
- Квартал 1 (январь–март)
- Квартал 2 (апрель–июнь)
- Квартал 3 (июль–сентябрь)
- Квартал 4 (октябрь–декабрь)
- Главный совет на год

Пиши минимум 800 слов, максимум 1500. Используй эмодзи умеренно (🔮🌙⭐✨)."""


# ══════════════════════════════════════════════════════════════════════════════
#  ШАГИ ДИАЛОГА
# ══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = [
        [InlineKeyboardButton(f"🔮 Натальная карта — {PRODUCTS['natal']['stars']} ⭐", callback_data="product_natal")],
        [InlineKeyboardButton(f"🌙 Прогноз 3 месяца — {PRODUCTS['forecast_3m']['stars']} ⭐", callback_data="product_forecast_3m")],
        [InlineKeyboardButton(f"✨ Прогноз на год — {PRODUCTS['forecast_year']['stars']} ⭐", callback_data="product_forecast_year")],
    ]
    await update.message.reply_text(
        "✨ *Добро пожаловать.*\n\n"
        "Я — Мирра. Астролог и провидица.\n\n"
        "Я не даю общих гороскопов. Только персональный анализ — "
        "по твоей дате, времени и месту рождения.\n\n"
        "Выбери, что тебе нужно сейчас:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return CHOOSING_PRODUCT


async def choose_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.replace("product_", "")
    product = PRODUCTS[product_key]
    context.user_data["product"] = product_key

    await query.edit_message_text(
        f"*{product['name']}*\n\n{product['desc']}\n\n"
        f"Стоимость: *${product['price_usd']}*\n\n"
        "Мне нужно несколько данных для составления прогноза.\n\n"
        "Начнём — как тебя зовут?",
        parse_mode="Markdown",
    )
    return ASKING_NAME


# ─── Сбор данных ──────────────────────────────────────────────────────────────
async def ask_birthdate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text(
        f"Хорошо, {context.user_data['name']}...\n\n"
        "Дата рождения — самое важное.\n"
        "Напиши в формате: *ДД.ММ.ГГГГ* _(например: 15.03.1990)_",
        parse_mode="Markdown",
    )
    return ASKING_BIRTHDATE


async def ask_birthtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birthdate"] = update.message.text.strip()
    await update.message.reply_text(
        "Время рождения — если знаешь, это важно для точности.\n\n"
        "Формат *ЧЧ:ММ* (например: 14:30)\n"
        "Если не знаешь — напиши _«не знаю»_",
        parse_mode="Markdown",
    )
    return ASKING_BIRTHTIME


async def ask_birthcity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birthtime"] = update.message.text.strip()
    await update.message.reply_text(
        "Город рождения — звёзды привязаны к месту.\n\n"
        "Напиши город и страну _(например: Киев, Украина)_",
        parse_mode="Markdown",
    )
    return ASKING_BIRTHCITY


async def ask_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["birthcity"] = update.message.text.strip()
    await update.message.reply_text(
        "Твой email — пришлю прогноз оформленным документом.\n\n"
        "_(Только для доставки)_",
        parse_mode="Markdown",
    )
    return ASKING_EMAIL


async def ask_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["email"] = update.message.text.strip()
    await update.message.reply_text(
        "Последнее... есть ли конкретный вопрос или область жизни, "
        "которая сейчас важнее всего?\n\n"
        "_(Отношения, деньги, работа, здоровье — что угодно. "
        "Или напиши «нет» для общего прогноза)_",
        parse_mode="Markdown",
    )
    return ASKING_QUESTION


# ══════════════════════════════════════════════════════════════════════════════
#  ВЫБОР СПОСОБА ОПЛАТЫ
# ══════════════════════════════════════════════════════════════════════════════

async def choose_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["question"] = update.message.text.strip()
    product = PRODUCTS[context.user_data["product"]]

    kb = [
        [InlineKeyboardButton(f"⭐ Telegram Stars ({product['stars']} XTR)", callback_data="pay_stars")],
        [InlineKeyboardButton(f"💎 TON ({product['ton']} TON)",               callback_data="pay_ton")],
        [InlineKeyboardButton(f"💵 USDT ({product['usdt']} USDT)",            callback_data="pay_usdt")],
        [InlineKeyboardButton(f"💳 Банковская карта (${product['price_usd']})", callback_data="pay_card")],
    ]
    await update.message.reply_text(
        f"✨ *Данные получены.*\n\n"
        f"Продукт: *{product['name']}*\n\n"
        "Выбери удобный способ оплаты:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return CHOOSING_PAYMENT


# ──────────────────────────────────────────────────────────────────────────────
#  1. TELEGRAM STARS — полностью автоматически
# ──────────────────────────────────────────────────────────────────────────────
async def pay_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product = PRODUCTS[context.user_data["product"]]

    await query.message.reply_text("Открываю окно оплаты Telegram Stars... ⭐")

    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=product["name"],
        description=product["desc"],
        payload=f"{context.user_data['product']}:{update.effective_user.id}",
        currency="XTR",
        prices=[LabeledPrice(product["name"], product["stars"])],
        # provider_token не нужен для Stars
    )
    return CHOOSING_PAYMENT   # ждём successful_payment


async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stars_paid = update.message.successful_payment.total_amount
    await _notify_admin(context, update.effective_user, "Stars", f"{stars_paid} ⭐")
    await update.message.reply_text(
        f"✅ *Оплата {stars_paid} Stars прошла!*\n\n"
        f"Читаю твою карту, {context.user_data.get('name')}... 🔮",
        parse_mode="Markdown",
    )
    await generate_and_send(update, context)
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────────────────────────
#  2. TON — показываем адрес, ждём скриншот
# ──────────────────────────────────────────────────────────────────────────────
async def pay_ton(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product = PRODUCTS[context.user_data["product"]]
    context.user_data["payment_method"] = "TON"

    await query.message.reply_text(
        f"💎 *Оплата в TON*\n\n"
        f"Сумма: *{product['ton']} TON*\n\n"
        f"Адрес кошелька:\n`{TON_WALLET}`\n\n"
        "_(Нажми на адрес чтобы скопировать)_\n\n"
        "После оплаты пришли сюда скриншот или хэш транзакции.",
        parse_mode="Markdown",
    )
    return WAITING_CRYPTO_PROOF


# ──────────────────────────────────────────────────────────────────────────────
#  3. USDT — показываем адрес, ждём скриншот
# ──────────────────────────────────────────────────────────────────────────────
async def pay_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product = PRODUCTS[context.user_data["product"]]
    context.user_data["payment_method"] = "USDT"

    await query.message.reply_text(
        f"💵 *Оплата в USDT (TRC-20)*\n\n"
        f"Сумма: *{product['usdt']} USDT*\n\n"
        f"Адрес кошелька:\n`{USDT_WALLET}`\n\n"
        "_(Нажми на адрес чтобы скопировать)_\n\n"
        "⚠️ Сеть: *TRC-20* (Tron) — не перепутай!\n\n"
        "После оплаты пришли скриншот или хэш транзакции.",
        parse_mode="Markdown",
    )
    return WAITING_CRYPTO_PROOF


# ──────────────────────────────────────────────────────────────────────────────
#  4. БАНКОВСКАЯ КАРТА — через Telegram Payments (Stripe)
# ──────────────────────────────────────────────────────────────────────────────
async def pay_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product = PRODUCTS[context.user_data["product"]]

    if not CARD_PROVIDER_TOKEN:
        await query.message.reply_text(
            "💳 Оплата картой временно недоступна.\n\n"
            "Пожалуйста, выбери другой способ оплаты или напиши /start.",
        )
        return CHOOSING_PAYMENT

    await query.message.reply_text("Открываю форму оплаты картой... 💳")

    # Telegram Payments: цена в центах (USD * 100)
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=product["name"],
        description=product["desc"],
        payload=f"card_{context.user_data['product']}:{update.effective_user.id}",
        provider_token=CARD_PROVIDER_TOKEN,   # токен от Stripe через BotFather
        currency="USD",
        prices=[LabeledPrice(product["name"], product["price_usd"] * 100)],
        # Можно добавить need_email=True если хочешь получать email от Telegram
    )
    return CHOOSING_PAYMENT   # ждём successful_payment


# ──────────────────────────────────────────────────────────────────────────────
#  Приём скриншота/хэша для крипты (TON / USDT)
# ──────────────────────────────────────────────────────────────────────────────
async def receive_crypto_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    method = context.user_data.get("payment_method", "Крипта")
    proof  = update.message.text or "📸 скриншот"

    await _notify_admin(context, update.effective_user, method, proof, photo=update.message.photo)

    await update.message.reply_text(
        "Получила подтверждение... ✨\n\n"
        "Проверяю транзакцию — обычно это занимает несколько минут.\n"
        "_Как только подтвержу — сразу начну составлять твой прогноз._ 🔮",
        parse_mode="Markdown",
    )
    # После проверки администратор вручную запускает /confirm USER_ID
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────────────────────────
#  Утилиты
# ──────────────────────────────────────────────────────────────────────────────
async def _notify_admin(context, user, method: str, proof: str, photo=None):
    if not ADMIN_ID:
        return
    ud = context.user_data
    product = PRODUCTS.get(ud.get("product", ""), {})
    text = (
        f"💰 *Новая оплата — {method}*\n\n"
        f"Пользователь: @{user.username or user.first_name} (ID: `{user.id}`)\n"
        f"Продукт: {product.get('name', '?')}\n"
        f"Способ: {method}\n"
        f"Доказательство: {proof}\n\n"
        f"Данные клиента:\n"
        f"Имя: {ud.get('name')}\n"
        f"Дата: {ud.get('birthdate')}\n"
        f"Время: {ud.get('birthtime')}\n"
        f"Город: {ud.get('birthcity')}\n"
        f"Email: {ud.get('email')}\n"
        f"Вопрос: {ud.get('question')}\n\n"
        f"▶️ Подтвердить: /confirm {user.id}"
    )
    try:
        await context.bot.send_message(ADMIN_ID, text, parse_mode="Markdown")
        if photo:
            await context.bot.send_photo(ADMIN_ID, photo[-1].file_id)
    except Exception as e:
        logger.error(f"Ошибка уведомления админа: {e}")


async def generate_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        forecast = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _generate_sync(context.user_data, context.user_data["product"]),
        )
        for i in range(0, len(forecast), 4000):
            await update.message.reply_text(forecast[i:i + 4000])

        await update.message.reply_text(
            f"✨ *Готово, {context.user_data.get('name')}.*\n\n"
            "Перечитай в тишине. Если появятся вопросы — напиши /start.\n\n"
            "_Мирра_ 🔮",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await update.message.reply_text("Возникла техническая помеха... Напишу чуть позже. 🔮")


def _generate_sync(user_data: dict, product_key: str) -> str:
    product = PRODUCTS[product_key]
    prompt = (
        f"Составь {product['name']} для следующего человека:\n\n"
        f"Имя: {user_data.get('name')}\n"
        f"Дата рождения: {user_data.get('birthdate')}\n"
        f"Время рождения: {user_data.get('birthtime', 'неизвестно')}\n"
        f"Город рождения: {user_data.get('birthcity')}\n"
        f"Вопрос или запрос: {user_data.get('question', 'не указан')}\n\n"
        "Составь персональный прогноз согласно своей роли."
    )
    response = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ─── /confirm — ручное подтверждение крипто-оплаты ────────────────────────────
async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /confirm USER_ID")
        return
    target_id = int(context.args[0])
    try:
        await context.bot.send_message(
            target_id,
            "✅ *Оплата подтверждена!*\n\nЧитаю твою карту... 🔮",
            parse_mode="Markdown",
        )
        # Достать user_data для target_id через bot_data (простое хранилище)
        stored = context.bot_data.get(str(target_id))
        if stored:
            # Имитируем update для generate_and_send
            class FakeMsg:
                async def reply_text(self, text, **kw):
                    await context.bot.send_message(target_id, text, **kw)
            class FakeUpd:
                message = FakeMsg()
                effective_user = update.effective_user
            fake_ctx = context
            fake_ctx.user_data.update(stored)
            await generate_and_send(FakeUpd(), fake_ctx)
        else:
            await update.message.reply_text(
                f"⚠️ Данные клиента {target_id} не найдены в памяти. "
                "Попроси его написать /start заново или введи данные вручную."
            )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Хорошо... Когда будешь готова — напиши /start. 🔮")
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════════════════
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING_PRODUCT: [
                CallbackQueryHandler(choose_product, pattern="^product_"),
            ],
            ASKING_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_birthdate)],
            ASKING_BIRTHDATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_birthtime)],
            ASKING_BIRTHTIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_birthcity)],
            ASKING_BIRTHCITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email)],
            ASKING_EMAIL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_question)],
            ASKING_QUESTION:  [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_payment_method)],
            CHOOSING_PAYMENT: [
                CallbackQueryHandler(pay_stars, pattern="^pay_stars$"),
                CallbackQueryHandler(pay_ton,   pattern="^pay_ton$"),
                CallbackQueryHandler(pay_usdt,  pattern="^pay_usdt$"),
                CallbackQueryHandler(pay_card,  pattern="^pay_card$"),
                # Stars и карта возвращают successful_payment как обычное сообщение
                MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment),
            ],
            WAITING_CRYPTO_PROOF: [
                MessageHandler(filters.TEXT | filters.PHOTO, receive_crypto_proof),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(CommandHandler("confirm", cmd_confirm))

    logger.info("Бот запущен — Stars / TON / USDT / Card ✅")
    app.run_polling()


if __name__ == "__main__":
    main()
