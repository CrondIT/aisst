"""Utility functions for billing operations,
coin management and payment callbacks."""

from telegram import Update
from telegram.ext import ContextTypes
import dbbot
from global_state import (
   COST_PER_MESSAGE,
)


def spend_coins(
    user_id: int,
    cost: int,
    coins: int,
    giftcoins: int,
    current_mode,
    user_message,
    reply,
):
    """--- ✅ Списываем монеты и записываем лог ---
    Если основных монет не хватило — списываем из подарочных
    """
    balance = coins + giftcoins
    remaining_cost = cost
    if coins >= remaining_cost:
        dbbot.change_all_coins(user_id, -remaining_cost, 0)
    else:
        # Сначала списываем с основных
        remaining_cost -= coins
        dbbot.change_all_coins(user_id, -coins, -remaining_cost)
    # --- ✅ СПИСАНИЕ ЗАВЕРШЕНО ---
    balance = balance - cost
    # LOGGING ====================
    log_text = f""" Запрос: {user_message}
        Ответ: {reply}
        """
    dbbot.log_action(
        user_id, current_mode, log_text, -cost, balance,
        "success", "billing_utils>spend_coins"
        )


async def check_user_coins(user_id: int, current_mode: str, context) -> tuple:
    """
    Проверяет наличие монет у пользователя.
    Возвращает (user_data, coins, giftcoins, balance, cost)
    или (None, 0, 0, 0, 0) если проверка не пройдена.
    """
    # Определяем стоимость в зависимости от режима
    cost = COST_PER_MESSAGE.get(current_mode)
    # Если стоимость не определена для режима
    # возвращаеи None и 0 монет
    if cost is None:
        print(f"стоимость не определена {current_mode}")
        return None, 0, 0, 0, 0
    # Получаем данные пользователя
    # (данные о балансе пользователя)
    user_data = dbbot.get_user(user_id)
    if not user_data:
        return None, 0, 0, 0, 0
    # Считаем общее количество монет
    coins = user_data["coins"]
    giftcoins = user_data["giftcoins"]
    balance = coins + giftcoins
    # Проверяем, хватает ли монет
    if balance < cost:
        # LOGGING ====================
        log_text = f""" У пользователя недостаточно средств
            Режим: {current_mode}
            Стоимость: {cost}
            Баланс: {balance}
            """
        dbbot.log_action(
            user_id, current_mode, log_text, 0, balance,
            "success", "billing_utils>check_user_coins"
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"⚠️ У вас недостаточно монет. "
                f"Стоимость запроса: {cost} монет.\n"
                f"Ваш баланс: {balance} монет.\n"
                f"Пополните счёт в /billing"
            ),
        )
        # ❌ Прерываем выполнение, если монет не хватает
        return None, 0, 0, 0, 0
    return user_data, coins, giftcoins, balance, cost


async def precheckout_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle pre-checkout queries for Telegram Stars payments."""
    query = update.pre_checkout_query

    # Check if the product is valid (we only accept specific coin packages)
    valid_products = {
        "coins50stars": {"coins": 50, "stars": 50},
        "coins100stars": {"coins": 100, "stars": 100},
        "coins500stars": {"coins": 500, "stars": 500},
    }

    if query.invoice_payload in valid_products:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Неверный продукт")


async def successful_payment_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle successful payments with Telegram Stars."""
    # Get the message with the successful payment
    successful_payment = update.message.successful_payment
    # Map invoice payloads to coin amounts
    product_map = {
        "coins50stars": {"coins": 50, "stars": 50},
        "coins100stars": {"coins": 100, "stars": 100},
        "coins500stars": {"coins": 500, "stars": 500},
    }
    # Get user ID from the payment
    user_id = update.effective_user.id
    user_data = dbbot.get_user(user_id)
    balance = user_data["coins"] + user_data["giftcoins"]
    current_mode = "billing"
    # Check if the invoice payload is valid
    if successful_payment.invoice_payload in product_map:
        product_info = product_map[successful_payment.invoice_payload]
        coins_to_add = product_info["coins"]
        stars_amount = product_info["stars"]

        # Add coins to user's account
        success = dbbot.change_all_coins(user_id, coins_to_add, 0)
        if success:
            # Get updated user info
            balance = user_data["coins"] + user_data["giftcoins"]
            # LOGGING ====================
            log_text = f""" Успешно приобретены монеты {coins_to_add}
                за звезды {stars_amount}
                Баланс монет: {balance}
                """
            dbbot.log_action(
                user_id, current_mode, log_text, coins_to_add, balance,
                "success", "billing_utils>successful_payment_callback"
            )
            # Send success message
            await update.message.reply_text(
                f"🎉 Вы приобрели {coins_to_add} монет за {stars_amount} ⭐️ "
                "Telegram Stars!\n"
                f"Ваш новый баланс: {balance} монет."
            )
        else:
            # LOGGING ====================
            log_text = f""" Ошибка при пополнении баланса
                {coins_to_add} монет за звезды {stars_amount}
                Баланс монет: {balance}
                """
            dbbot.log_action(user_id, current_mode, log_text, 0, balance,
                             "error",
                             "billing_utils>successful_payment_callback"
                             )
            await update.message.reply_text(
                "❌ Произошла ошибка при пополнении баланса. "
                "Пожалуйста, свяжитесь с поддержкой."
            )
    else:
        # LOGGING ====================
        log_text = f""" Неизвестный продукт (при покупке монет за звезды)
            {coins_to_add} монет за звезды {stars_amount}
            Баланс монет: {balance}
            """
        dbbot.log_action(user_id, current_mode, log_text, 0, balance,
                         "error",
                         "billing_utils>successful_payment_callback"
                         )
        await update.message.reply_text(
            "❌ Неизвестный продукт. "
            "Пожалуйста, используйте кнопки в меню /billing."
        )
