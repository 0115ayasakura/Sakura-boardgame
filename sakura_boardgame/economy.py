"""Explicit cash policy; no balance mutation, RNG or insolvency handling.

Asset liquidation deliberately has two policies: voluntary proceeds historically
receive income effects, forced mortgage proceeds do not. Rent receipts likewise
retain the legacy exception: gain alone does not amplify rent, adventure does.
Adventure settlement is already capped by its own ledger and is never multiplied.
"""
from enum import Enum
from typing import Mapping, Any


class CashCategory(str, Enum):
    INCOME = "income"
    RENT_RECEIPT = "rent_receipt"
    ACTIVE_PRICE = "active_price"
    PASSIVE_EXPENSE = "passive_expense"
    ASSET_VOLUNTARY = "asset_voluntary"
    ASSET_FORCED = "asset_forced"
    ADVENTURE_SETTLEMENT = "adventure_settlement"


def cash_amount(amount: int, category: CashCategory, flags: Mapping[str, Any], *,
                adventure_active: bool, gain_rate: float, loss_rate: float,
                loss_up_rate: float, adventure_rate: float) -> int:
    """Quote a signed amount. Rounding and maximum (not stacking) are legacy rules."""
    category = CashCategory(category)  # reject an unclassified transaction
    if category in (CashCategory.ACTIVE_PRICE, CashCategory.ASSET_FORCED,
                    CashCategory.ADVENTURE_SETTLEMENT) or (
            category == CashCategory.RENT_RECEIPT and not adventure_active):
        return amount  # nominal amounts never detour through float/round
    multiplier = 1.0
    if category in (CashCategory.INCOME, CashCategory.ASSET_VOLUNTARY) or (
            category == CashCategory.RENT_RECEIPT and adventure_active):
        multiplier = gain_rate if flags.get("buff") == "gain" else 1.0
        if adventure_active:
            multiplier = max(multiplier, adventure_rate)
    elif category == CashCategory.PASSIVE_EXPENSE and amount:
        multiplier = {"loss": loss_rate, "loss_up": loss_up_rate}.get(flags.get("buff"), 1.0)
        if adventure_active:
            multiplier = max(multiplier, adventure_rate)
    return round(amount * multiplier)
