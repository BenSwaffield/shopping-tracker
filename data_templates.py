from dataclasses import dataclass


@dataclass
class ReceiptData:
    merchant_name: str
    date: str
    items: list
    id: int = None


@dataclass
class ReceiptItem:
    name: str
    quantity: int
    price_per_item: float
    id: int = None