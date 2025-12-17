import os
import re

from flask import Flask, render_template, request, g, redirect
import sqlite3

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.exceptions import HttpResponseError

from data_templates import *

AZURE_FORM_RECOGNIZER_ENDPOINT = os.environ.get("AZURE_FORM_RECOGNIZER_ENDPOINT")
AZURE_FORM_RECOGNIZER_KEY = os.environ.get("AZURE_FORM_RECOGNIZER_KEY")
DATABASE = 'data/database.db'


app = Flask(__name__)

# Database connection management

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    return db

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Initialize the database and create tables if they don't exist."""
    with app.app_context():
        db = get_db()
        with db:
            # Create receipts table
            db.execute('''
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    merchant_name TEXT,
                    date TEXT,
                    purchaser_name TEXT,
                    uploaded_at DATETIME NOT NULL
                );
            ''')
            # Create receipt_items table
            db.execute('''
                CREATE TABLE IF NOT EXISTS receipt_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id INTEGER NOT NULL,
                    item_name TEXT,
                    item_quantity INTEGER,
                    item_cost FLOAT,
                    FOREIGN KEY (receipt_id) REFERENCES receipts (id)
                );
            ''')

# Endpoints

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        if "file" not in request.files:
            return "No file part", 400
        file = request.files["file"]
        if file.filename == "":
            return "No selected file", 400
        if file:
            image_data = file.read()
            result = call_azure_form_recognizer(image_data)
            receipt_data = parse_receipt_data(result)
            add_receipt_to_db(receipt_data)#
            return redirect(f"/receipts/{receipt_data.id}")
            # return render_template("receipt_view.html", receipt=receipt_data)
    
    return render_template("index.html")

@app.route("/receipts/<id>", methods=["GET"])
def receipt_page(id):
    receipt_data = get_receipt_data(id)
    print(receipt_data)
    return render_template("receipt_view.html", receipt=receipt_data, items=receipt_data.items)

@app.route("/view/receipt-item/<id>", methods=["GET"])
def view_receipt_item(id):
    print(id)
    if id == "new":
        return render_template("item.html", item=ReceiptItem(name="", quantity=1, price_per_item=0.0))
    item = get_receipt_item(id)
    if item is None:
        return "Receipt item not found", 404
    else:
        return render_template("item.html", item=item)

def call_azure_form_recognizer(image_data):
    document_intelligence_client = DocumentIntelligenceClient(
        endpoint=AZURE_FORM_RECOGNIZER_ENDPOINT,
        credential=AzureKeyCredential(AZURE_FORM_RECOGNIZER_KEY),
    )

    request = AnalyzeDocumentRequest(bytes_source=image_data)

    try:
        poller = document_intelligence_client.begin_analyze_document(
            "prebuilt-receipt", request
        )

        result = poller.result()
        return result

    except HttpResponseError as e:
        print(f"Error occurred: {e.message}")
        return None


def parse_receipt_data(result):
    print(result.content.split("\n")[0].lower())
    if "aldi" in result.content.split("\n")[0].lower():
        return parse_aldi_receipt_data(result)
    else:
        raise NotImplementedError(
            "Receipt parser for this merchant is not implemented."
        )


def parse_aldi_receipt_data(result):
    content = result.content.split("\n")
    receipt_items = []
    i = 0
    # Find start of items
    while content[i] != "GBP":
        print(f"Skipping line: {content[i]}")
        i += 1
    i += 1  # Move past "GBP"

    while i < len(content):
        print(f"Processing line: {content[i]}")
        if content[i] == "Subtotal" or content[i] == "Total":
            break
        if re.search(r"\d+x", content[i].replace(" ", "")):
            print("Multiple quantity format detected.")
            receipt_items.append(
                ReceiptItem(
                    name=content[i + 2],
                    quantity=int(content[i].replace("x", "").strip()),
                    price_per_item=float(content[i + 1]),
                )
            )
            i += 4
        else:
            receipt_items.append(
                ReceiptItem(
                    name=content[i],
                    quantity=1,
                    price_per_item=float(content[i + 1].strip().split(" ")[0]),
                )
            )
            i += 2
        print(f"Added item: {receipt_items[-1]}")

    print(receipt_items)
    receipt_data = ReceiptData(
        merchant_name="Aldi",
        date="Unknown",
        items=receipt_items,
    )
    return receipt_data

def add_receipt_to_db(receipt_data):
    db = get_db()
    with db:
        cursor = db.execute('''
            INSERT INTO receipts (merchant_name, date, purchaser_name, uploaded_at)
            VALUES (?, ?, ?, datetime('now'))
        ''', (receipt_data.merchant_name, receipt_data.date, "Unknown"))
        receipt_id = cursor.lastrowid

        for item in receipt_data.items:
            db.execute('''
                INSERT INTO receipt_items (receipt_id, item_name, item_quantity, item_cost)
                VALUES (?, ?, ?, ?)
            ''', (receipt_id, item.name, item.quantity, item.price_per_item))

def get_receipt_item(item_id) -> ReceiptItem:
    item = query_db('''
        SELECT * FROM receipt_items WHERE id = ?
    ''', (item_id,))
    return ReceiptItem(
        id=item[0]['id'],
        name=item[0]['item_name'],
        quantity=item[0]['item_quantity'],
        price_per_item=item[0]['item_cost']
    ) if item else None

def update_receipt_item(item: ReceiptItem):
    db = get_db()
    with db:
        db.execute('''
            UPDATE receipt_items
            SET item_name = ?, item_quantity = ?, item_cost = ?
            WHERE id = ?
        ''', (item.name, item.quantity, item.price_per_item, item.id))

def get_receipt_items(receipt_id) -> list:
    items = query_db('''
        SELECT * FROM receipt_items WHERE receipt_id = ?
    ''', (receipt_id,))
    return [
        ReceiptItem(
            id=item['id'],
            name=item['item_name'],
            quantity=item['item_quantity'],
            price_per_item=item['item_cost']
        ) for item in items
    ]

def get_receipt_data(receipt_id) -> ReceiptData:
    receipt = query_db('''
        SELECT * FROM receipts WHERE id = ?
    ''', (receipt_id,), one=True)
    items = get_receipt_items(receipt_id)
    return ReceiptData(
        id=receipt['id'],
        merchant_name=receipt['merchant_name'],
        date=receipt['date'],
        items=items
    )

if __name__ == "__main__":
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
    # print("Reading test image...")
    # image = open("image_20251112154119.jpg", "rb").read()
    # print("Calling Azure Form Recognizer...")
    # result = call_azure_form_recognizer(image)
    # print(result.content)
    # receipt_data = parse_receipt_data(result)
