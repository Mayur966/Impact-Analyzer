from math_utils import calculate_total
from discounts import final_price


def process_order(items, discount_percent):
    total = calculate_total(items)
    return final_price(total, discount_percent)
