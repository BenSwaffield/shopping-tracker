import os
import re

from flask import Flask

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from azure.core.exceptions import HttpResponseError

from data_templates import *

AZURE_FORM_RECOGNIZER_ENDPOINT = os.environ.get("AZURE_FORM_RECOGNIZER_ENDPOINT")
AZURE_FORM_RECOGNIZER_KEY = os.environ.get("AZURE_FORM_RECOGNIZER_KEY")


app = Flask(__name__)


@app.route("/")
def home():
    return "Welcome to the Shopping Tracker App!"


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


if __name__ == "__main__":
    # app.run(host='0.0.0.0', port=5000, debug=True)
    print("Reading test image...")
    image = open("image_20251112154119.jpg", "rb").read()
    print("Calling Azure Form Recognizer...")
    result = call_azure_form_recognizer(image)
    print(result.content)
    receipt_data = parse_receipt_data(result)
