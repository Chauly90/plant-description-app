import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import SYSTEM_PROMPT, build_prompt, parse_all, assemble_html
import anthropic

HEADERS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": HEADERS, "body": ""}

    try:
        body = json.loads(event.get("body") or "{}")
        plant_name = body.get("plant_name", "").strip()

        if not plant_name:
            return {"statusCode": 400, "headers": HEADERS,
                    "body": json.dumps({"error": "Please enter a plant name."})}

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return {"statusCode": 500, "headers": HEADERS,
                    "body": json.dumps({"error": "ANTHROPIC_API_KEY not set"})}

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": build_prompt(plant_name)}],
        )

        full_text = message.content[0].text
        title, meta, tab1, tab2, tab3 = parse_all(full_text)

        if not tab1:
            return {"statusCode": 500, "headers": HEADERS,
                    "body": json.dumps({"error": "Could not parse response. Please try again."})}

        html = assemble_html(tab1, tab2, tab3, plant_name)
        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({"html": html, "title": title or "", "meta": meta or ""}),
        }

    except anthropic.APIError as e:
        return {"statusCode": 500, "headers": HEADERS, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        return {"statusCode": 500, "headers": HEADERS,
                "body": json.dumps({"error": f"Unexpected error: {str(e)}"})}
