import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import parse_all, assemble_html

HEADERS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": HEADERS, "body": ""}

    try:
        body = json.loads(event.get("body") or "{}")
        text = body.get("text", "")
        plant_name = body.get("plant_name", "")
        title, meta, tab1, tab2, tab3 = parse_all(text)
        if not tab1:
            return {"statusCode": 400, "headers": HEADERS,
                    "body": json.dumps({"error": "Could not parse"})}
        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({
                "html": assemble_html(tab1, tab2, tab3, plant_name),
                "title": title or "",
                "meta": meta or "",
            }),
        }
    except Exception as e:
        return {"statusCode": 500, "headers": HEADERS, "body": json.dumps({"error": str(e)})}
