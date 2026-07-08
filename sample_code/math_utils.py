def add(a, b):
    return a + b


def multiply(a, b):
    return a * b


def calculate_total(items):
    total = 0
    for item in items:
        total = add(total, item)
    return total
