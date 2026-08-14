from decimal import Decimal, InvalidOperation

from iso4217 import Currency

from cashsathi_api.errors import ApiError


def decimal_to_minor(amount: str, currency_code: str) -> int:
    """Convert a user-reviewed decimal string to integer ISO-4217 minor units."""
    try:
        currency = Currency(currency_code.upper())
    except ValueError:
        raise ApiError(422, "invalid_currency", "Use a valid ISO 4217 currency code.") from None
    try:
        decimal_amount = Decimal(amount.strip())
    except InvalidOperation:
        raise ApiError(422, "invalid_amount", "Enter a valid decimal invoice amount.") from None
    if not decimal_amount.is_finite() or decimal_amount <= 0:
        raise ApiError(422, "invalid_amount", "Invoice amount must be greater than zero.")
    if currency.exponent is None:
        raise ApiError(422, "invalid_currency", "Use a currency with defined minor units.")
    scale = Decimal(10) ** currency.exponent
    minor = decimal_amount * scale
    if minor != minor.to_integral_value():
        raise ApiError(
            422,
            "invalid_amount_precision",
            f"{currency_code.upper()} supports {currency.exponent} decimal places.",
        )
    return int(minor)
