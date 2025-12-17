from dataclasses import dataclass


@dataclass
class ReceiptData:
    store_name: str
    date: str
    items: list
    purchaser: str = ""
    id: int = None
    uploaded_at: str = "" 


@dataclass
class ReceiptItem:
    name: str
    quantity: int
    price_per_item: float
    id: int = None