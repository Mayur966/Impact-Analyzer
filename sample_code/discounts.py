from math_utils import multiply


def apply_discount(price, percent):
    discount_amount = multiply(price, percent) / 100
    return price - discount_amount


def final_price(price, percent):
    return apply_discount(price, percent)
