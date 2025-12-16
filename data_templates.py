from dataclasses import dataclass


@dataclass
class ReceiptData:
    merchant_name: str
    date: str
    items: list


@dataclass
class ReceiptItem:
    name: str
    quantity: int
    price_per_item: float
