import os
import re

from flask import Flask, render_template, request, g, redirect
import sqlite3
import yaml

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
                    store_name TEXT,
                    date TEXT,
                    purchaser_name TEXT,
                    settled BOOLEAN DEFAULT 0,
                    uploaded_at DATETIME NOT NULL
                );
            ''')
            # Create receipt_items table
            db.execute('''
                CREATE TABLE IF NOT EXISTS receipt_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id INTEGER NOT NULL,
                    share_cost BOOLEAN DEFAULT 1,
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
            # save to log
            with open("data/log.txt", "a") as log_file:
                log_file.write(result.content + "\n\n")
            receipt_data = parse_receipt_data(result)
            print("Parsed receipt data:", receipt_data)
            receipt_id = add_receipt_to_db(receipt_data)
            return redirect(f"/receipts/{receipt_id}")
            # return render_template("receipt_view.html", receipt=receipt_data)
    
    return render_template("index.html")

@app.route("/receipts", methods=["GET"])
def receipts_list():
    receipts = get_all_receipts()
    return render_template("receipts_list.html", receipts=receipts)

@app.route("/receipts/<id>", methods=["GET"])
def receipt_page(id):
    receipt_data = get_receipt_data(id)
    if receipt_data is None:
        return "Receipt not found", 404
    return render_template("receipt_view.html", receipt=receipt_data, items=receipt_data.items)

@app.route("/receipts-item/<receipt_id>/new", methods=["GET"])
def new_receipt_item(receipt_id):
    item = ReceiptItem(name="", quantity=1, price_per_item=0.0)
    item.id = add_receipt_item_to_db(receipt_id, item)
    return render_template("item.html", item=item)

@app.route("/receipt-item/<id>/edit", methods=["GET"])
def edit_receipt_item(id):
    item = get_receipt_item(id)
    if item is None:
        return "Receipt item not found", 404
    else:
        return render_template("edit_item.html", item=item)

@app.route("/receipt-item/<id>", methods=["GET", "PUT", "DELETE"])
def view_receipt_item(id):
    if request.method == "PUT":
        name = request.form.get("name")
        quantity = int(request.form.get("quantity"))
        price_per_item = float(request.form.get("price_per_item"))
        if request.form.get("share_cost"):
            share_cost = True
        else:
            share_cost = False
        item = ReceiptItem(
            id=int(id),
            name=name,
            quantity=quantity,
            price_per_item=price_per_item,
            share_cost=share_cost
        )
        update_receipt_item(item)
        return render_template("item.html", item=item)
    elif request.method == "DELETE":
        db = get_db()
        with db:
            db.execute('''
                DELETE FROM receipt_items WHERE id = ?
            ''', (id,))
        return ""
    print(id)
    if id == "new":
        return render_template("item.html", item=ReceiptItem(name="", quantity=1, price_per_item=0.0))
    item = get_receipt_item(id)
    if item is None:
        return "Receipt item not found", 404
    else:
        return render_template("item.html", item=item)

@app.route("/receipt-data/<id>/edit", methods=["GET"])
def edit_receipt_data(id):
    receipt = get_receipt_data(id)
    if receipt is None:
        return "Receipt not found", 404
    else:
        return render_template("edit_receipt_details.html", receipt=receipt)
    
@app.route("/receipt-data/<id>", methods=["GET", "PUT", "DELETE"])
def view_receipt_data(id):
    if id == "new":
        receipt_id = add_receipt_to_db(ReceiptData(store_name="", date="", items=[]))
        return render_template("receipt.html", receipt=ReceiptData(id=receipt_id, store_name="", date="", items=[]))
    if request.method == "PUT":
        store_name = request.form.get("store_name")
        date = request.form.get("date")
        purchaser = request.form.get("purchaser")
        receipt = get_receipt_data(id)
        receipt.store_name = store_name
        receipt.date = date
        receipt.purchaser = purchaser
        db = get_db()
        with db:
            db.execute('''
                UPDATE receipts
                SET store_name = ?, date = ?, purchaser_name = ?
                WHERE id = ?
            ''', (receipt.store_name, receipt.date, receipt.purchaser, receipt.id))
        return render_template("receipt.html", receipt=receipt)
    elif request.method == "DELETE":
        db = get_db()
        with db:
            db.execute('''
                DELETE FROM receipt_items WHERE receipt_id = ?
            ''', (id,))
            db.execute('''
                DELETE FROM receipts WHERE id = ?
            ''', (id,))
        return ""
    receipt = get_receipt_data(id)
    if receipt is None:
        return "Receipt not found", 404
    else:
        return render_template("receipt.html", receipt=receipt)
    
@app.route("/receipt-details/<id>/edit", methods=["GET"])
def edit_receipt_details(id):
    receipt = get_receipt_data(id)
    if receipt is None:
        return "Receipt not found", 404
    else:
        return render_template("edit_receipt_details.html", receipt=receipt)

@app.route("/receipt-details/<id>", methods=["GET", "PUT"])
def receipt_details(id):
    if request.method == "PUT":
        store_name = request.form.get("store_name")
        date = request.form.get("date")
        purchaser = request.form.get("purchaser")
        receipt = get_receipt_data(id)
        receipt.store_name = store_name
        receipt.date = date
        receipt.purchaser = purchaser
        db = get_db()
        with db:
            db.execute('''
                UPDATE receipts
                SET store_name = ?, date = ?, purchaser_name = ?
                WHERE id = ?
            ''', (receipt.store_name, receipt.date, receipt.purchaser, receipt.id))
        return render_template("receipt_details.html", receipt=receipt)
    receipt = get_receipt_data(id)
    if receipt is None:
        return "Receipt not found", 404
    else:
        return render_template("receipt_details.html", receipt=receipt)

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
    print("Store: " + result.content.split("\n")[0].lower())
    if "aldi" in result.content.lower():
        return parse_aldi_receipt_data(result)
    else:
        raise NotImplementedError(
            "Receipt parser for this store is not implemented."
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
        try:
            print(f"Processing line: {content[i]}")
            if content[i] == "Subtotal" or content[i] == "Total":
                break
            if re.search(r"\d+x", content[i].replace(" ", "")):
                print("Multiple quantity format detected.")
                receipt_items.append(
                    ReceiptItem(
                        name=content[i + 2],
                        quantity=int(content[i].replace("x", "").strip()),
                        price_per_item=float(content[i + 1].replace(",", ".")),
                    )
                )
                i += 4
            else:
                receipt_items.append(
                    ReceiptItem(
                        name=content[i],
                        quantity=1,
                        price_per_item=float(content[i + 1].strip().split(" ")[0].replace(",", ".")),
                    )
                )
                i += 2
            print(f"Added item: {receipt_items[-1]}")
        except Exception as e:
            print(f"Error processing line '{content[i]}': {e}")
            break

    # Attempt to find purchaser by card last 4 digits
    purchaser = "Unknown"
    for line in content:
        if "********" in line:
            strp_line = line.replace(" ", "")
            i = strp_line.rfind("*")
            if i == -1:
                continue
            card_last4 = strp_line[i + 1 : i + 5]
            purchaser = match_purchaser_by_card_last4(card_last4)
            print(f"Matched purchaser: {purchaser} for card ending in {card_last4}")
            break
    
    # Find date
    date_pattern = re.compile(r"\d\d[.]\d\d[.]\d\d\s*\d\d[:]\d\d")
    date_found = False
    for line in content:
        match = date_pattern.search(line)
        if match:
            date_str = match.group(0)
            date_found = True
            break
    receipt_data = ReceiptData(
        store_name="Aldi",
        date=date_str if date_found else "Unknown",
        items=receipt_items,
        purchaser=purchaser
    )
    return receipt_data

def match_purchaser_by_card_last4(card_last4):
    with open("data/config.yaml", "r") as f:
        config = yaml.safe_load(f)
    for purchaser in config.get("purchasers", []):
        if purchaser.get("card_last4") == card_last4:
            return purchaser.get("name")
    return "Unknown"

def add_receipt_to_db(receipt_data):
    db = get_db()
    with db:
        cursor = db.execute('''
            INSERT INTO receipts (store_name, date, purchaser_name, uploaded_at)
            VALUES (?, ?, ?, datetime('now'))
        ''', (receipt_data.store_name, receipt_data.date, receipt_data.purchaser))
        receipt_id = cursor.lastrowid

        for item in receipt_data.items:
            db.execute('''
                INSERT INTO receipt_items (receipt_id, item_name, item_quantity, item_cost, share_cost)
                VALUES (?, ?, ?, ?, ?)
            ''', (receipt_id, item.name, item.quantity, item.price_per_item, item.share_cost))
    return receipt_id

def add_receipt_item_to_db(receipt_id, item: ReceiptItem):
    db = get_db()
    with db:
        cursor = db.execute('''
            INSERT INTO receipt_items (receipt_id, item_name, item_quantity, item_cost, share_cost)
            VALUES (?, ?, ?, ?, ?)
        ''', (receipt_id, item.name, item.quantity, item.price_per_item, item.share_cost))
        item_id = cursor.lastrowid
    return item_id

def get_receipt_item(item_id) -> ReceiptItem:
    item = query_db('''
        SELECT * FROM receipt_items WHERE id = ?
    ''', (item_id,))
    return ReceiptItem(
        id=item[0]['id'],
        name=item[0]['item_name'],
        quantity=item[0]['item_quantity'],
        price_per_item=item[0]['item_cost'],
        share_cost=item[0]['share_cost']
    ) if item else None

def update_receipt_item(item: ReceiptItem):
    db = get_db()
    with db:
        db.execute('''
            UPDATE receipt_items
            SET item_name = ?, item_quantity = ?, item_cost = ?, share_cost = ?
            WHERE id = ?
        ''', (item.name, item.quantity, item.price_per_item, item.share_cost, item.id))

def get_receipt_items(receipt_id) -> list:
    items = query_db('''
        SELECT * FROM receipt_items WHERE receipt_id = ?
    ''', (receipt_id,))
    return [
        ReceiptItem(
            id=item['id'],
            name=item['item_name'],
            quantity=item['item_quantity'],
            price_per_item=item['item_cost'],
            share_cost=item['share_cost']
        ) for item in items
    ]

def get_receipt_data(receipt_id) -> ReceiptData:
    receipt = query_db('''
        SELECT * FROM receipts WHERE id = ?
    ''', (receipt_id,), one=True)
    if receipt is None:
        return None
    items = get_receipt_items(receipt_id)
    return ReceiptData(
        id=receipt['id'],
        store_name=receipt['store_name'],
        date=receipt['date'],
        purchaser=receipt['purchaser_name'],
        items=items,
        uploaded_at=receipt['uploaded_at']
    )

def get_all_receipts() -> list:
    receipts = query_db('''
        SELECT * FROM receipts ORDER BY uploaded_at DESC
    ''')
    return [
        ReceiptData(
            id=receipt['id'],
            store_name=receipt['store_name'],
            date=receipt['date'],
            items=[],
            uploaded_at=receipt['uploaded_at'],
            purchaser=receipt['purchaser_name']
        ) for receipt in receipts
    ]

if __name__ == "__main__":
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
    # print("Reading test image...")
    # image = open("image_20251112154119.jpg", "rb").read()
    # print("Calling Azure Form Recognizer...")
    # result = call_azure_form_recognizer(image)
    # print(result.content)
    # receipt_data = parse_receipt_data(result)
