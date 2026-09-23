def scale_price(price: float, decimals: int) -> int:
    return int(round(price * (10 ** decimals)))


def unscale_price(scaled: int, decimals: int) -> float:
    return scaled / (10 ** decimals)


def scale_size(size: float, decimals: int) -> int:
    return int(round(size * (10 ** decimals)))


def unscale_size(scaled: int, decimals: int) -> float:
    return scaled / (10 ** decimals)
