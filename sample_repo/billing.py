"""billing.py - Financial billing calculations and monetary rounding utilities."""

def round_money(amount: float) -> float:
    """Rounds monetary values to two decimal precision."""
    return round(amount, 2)


def compute_tax(amount: float, rate: float = 0.18) -> float:
    """Calculates tax on a given monetary sum with rounding applied."""
    raw_tax = amount * rate
    return round_money(raw_tax)
