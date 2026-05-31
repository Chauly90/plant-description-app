import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _shared import AMAZON_SYSTEM_PROMPT, build_amazon_prompt, parse_amazon
import anthropic

HEADERS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": HEADERS, "body": ""}

    try:
        body = json.loads(event.get("body") or "{}")
        plant_name   = body.get("plant_name", "").strip()
        pot_sizes    = body.get("pot_sizes", ["2"])
        if isinstance(pot_sizes, str):
            pot_sizes = [pot_sizes]
        shopify_desc = body.get("shopify_desc", "")

        if not plant_name:
            return {"statusCode": 400, "headers": HEADERS,
                    "body": json.dumps({"error": "Plant name is required"})}
        if not pot_sizes:
            return {"statusCode": 400, "headers": HEADERS,
                    "body": json.dumps({"error": "Please select at least one size"})}

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return {"statusCode": 500, "headers": HEADERS,
                    "body": json.dumps({"error": "ANTHROPIC_API_KEY not set"})}

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=[{"type": "text", "text": AMAZON_SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user",
                       "content": build_amazon_prompt(plant_name, pot_sizes, shopify_desc)}],
        )

        result = parse_amazon(message.content[0].text)
        return {"statusCode": 200, "headers": HEADERS, "body": json.dumps(result)}

    except anthropic.APIError as e:
        return {"statusCode": 500, "headers": HEADERS, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        return {"statusCode": 500, "headers": HEADERS,
                "body": json.dumps({"error": f"Unexpected error: {str(e)}"})}
