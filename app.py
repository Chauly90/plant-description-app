import json
import os
import re
from flask import Flask, request, jsonify, render_template, Response, stream_with_context
import anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

ICONS = {
    "cactus":      "https://cdn.shopify.com/s/files/1/2198/4603/files/Cactus_26a62e4c-2f57-4ecf-97de-fc345c4a381c_480x480.png?v=1600401339",
    "house":       "https://cdn.shopify.com/s/files/1/2198/4603/files/House_8db6c6e9-d5bb-466c-95af-bdbcbe9ff370_480x480.png?v=1600400127",
    "sun":         "https://cdn.shopify.com/s/files/1/2198/4603/files/Sun_65296975-26d8-49a3-83b6-b7b0ed15ae73_480x480.png?v=1600397323",
    "water":       "https://cdn.shopify.com/s/files/1/2198/4603/files/Water_d9b740b9-bfce-41c4-ae3e-16bf9678c77f_480x480.png?v=1600397177",
    "soil":        "https://cdn.shopify.com/s/files/1/2198/4603/files/Soil_40a149eb-9d43-4bf0-ac38-159c702c72f2_480x480.png?v=1600397158",
    "temperature": "https://cdn.shopify.com/s/files/1/2198/4603/files/Temperature_f0d54887-2130-48b9-9020-c9b8cdec1f12_480x480.png?v=1600399389",
    "fertilizer":  "https://cdn.shopify.com/s/files/1/2198/4603/files/Fertilizer_5853ff19-6010-410a-a082-3abe233cb8d2_480x480.png?v=1600399297",
}


def icon(name):
    return f'<img data-mce-fragment="1" width="16" height="16" data-src="{ICONS[name]}" class="lazyload" alt="">'


SYSTEM_PROMPT = f"""You are a professional plant product description writer for succulentsbox.com. Write accurate, engaging, SEO-friendly descriptions in English.

WRITING RULES — follow strictly:
1. CONCISE: Keep descriptions about 30% shorter than typical. Every sentence must add value. No filler.
2. NO EM DASHES: Never use — or – in any text. Use commas or rewrite instead.
3. NO BOLD TEXT: Never wrap any word in ** or <b> or <strong>.
4. SKIP IF NOT APPLICABLE: If a feature does not apply to this plant (e.g. it does not purify air, does not release oxygen at night, has no culinary use), simply omit it. Do not write "no" answers.
5. PET SAFETY LANGUAGE:
   - If the plant IS safe for pets: write "This plant is pet friendly" naturally in the text.
   - If the plant is NOT safe for pets: use soft language such as "best placed away from curious cats and dogs" or "keep it out of reach of pets as nibbling the leaves may cause stomach upset." NEVER use the words: toxic, non-toxic, poisonous, or any variation.
6. CHILD SAFETY LANGUAGE: Same rule as pets. Use gentle phrasing like "place out of reach of young children" or "the sap may irritate sensitive skin, so wash hands after handling." NEVER use: toxic, poisonous, dangerous, hazardous.
7. NO REPETITION: Do not repeat the same information across paragraphs or tabs.

CONTENT TO COVER per plant:

TAB1 — Description: write exactly 3 separate paragraphs, each focused on ONE topic only:
- Para 1 (identity): common name, scientific name, nicknames, brief origin/family
- Para 2 (appearance): leaf/stem shape, color, texture, size, growth habit, best pot style
- Para 3 (benefits & home): practical uses (healing, culinary, air purification only if true), pet/child safety note, where to place it at home

TAB2 — Care: 5 paragraphs, one per topic. No overlap with TAB1.
- Light: specific needs, tolerance, risks
- Water: frequency, soil-check method, drainage tips
- Soil: best mix, amendments
- Temperature & Humidity: ideal °F range, cold minimum, humidity preference
- Fertilizer: schedule and type

TAB3 — USDA Hardiness: include ALL of the following for this specific plant:
- The USDA zone map image (always include)
- Zone range (e.g., "Zones 9-11")
- Minimum survival temperature in both °F and °C
- Whether it is grown outdoors year-round or must be brought indoors in cold climates
- One sentence on frost tolerance or winter care if relevant

OUTPUT FORMAT — Start your response with the EXACT text ===TAB1=== on its own line. Output ONLY the three sections below, nothing before or after, no preamble:

===TAB1===
Exactly 3 paragraphs separated by <br><br>
- Para 1 (identity: name, scientific name, origin) starts with: {icon('cactus')}
- Para 2 (appearance: shape, color, texture, size, pot style) starts with: {icon('soil')}
- Para 3 (benefits, uses, pet/child safety, where to place) starts with: {icon('house')}

===TAB2===
5 paragraphs separated by <br><br>, each starting with its icon:
{icon('sun')} {icon('water')} {icon('soil')} {icon('temperature')} {icon('fertilizer')}

===TAB3===
<p><img width="1024" height="887" data-src="//cdn.shopify.com/s/files/1/2198/4603/files/map-of-growing-zones-usa-17-01_1024x1024.jpg?v=1553694426" class="lazyload" alt=""></p>
<p>USDA Hardiness Zones [X]-[Y] | Minimum temperature: [°F] ([°C])</p>
<p>[One sentence: outdoor year-round suitability and/or frost/winter care note.]</p>"""


def build_prompt(plant_name: str) -> str:
    return f"""Plant: {plant_name}

Write the complete 3-tab product description. Follow all writing rules exactly.
Use the correct icons, separate paragraphs with <br><br>, include accurate USDA zones for {plant_name}.
Remember: no em dashes, no bold, skip inapplicable features, use soft pet/child safety language."""


def parse_tabs(text: str):
    import sys
    # Try strict match first: ===TAB1=== style (any number of = signs)
    t1 = re.search(r"=+\s*TAB1\s*=+\s*(.*?)\s*=+\s*TAB2\s*=+", text, re.DOTALL)
    t2 = re.search(r"=+\s*TAB2\s*=+\s*(.*?)\s*=+\s*TAB3\s*=+", text, re.DOTALL)
    t3 = re.search(r"=+\s*TAB3\s*=+\s*(.*?)(?:\s*=+|$)", text, re.DOTALL)
    if t1 and t2 and t3:
        return t1.group(1).strip(), t2.group(1).strip(), t3.group(1).strip()

    # Fallback: try splitting by TAB markers without equals
    parts = re.split(r"=*\s*TAB[123]\s*=*", text)
    if len(parts) >= 4:
        print(f"[PARSE_FALLBACK] Used split fallback", file=sys.stderr, flush=True)
        return parts[1].strip(), parts[2].strip(), parts[3].strip()

    print(f"[PARSE_FAILED] First 500 chars: {text[:500]!r}", file=sys.stderr, flush=True)
    return None, None, None


def assemble_html(tab1: str, tab2: str, tab3: str) -> str:
    return (
        '<ul class="tabs">\n'
        '<li><a href="#tab1" class="active">Description</a></li>\n'
        '<li><a href="#tab2">Light/Soil/Water</a></li>\n'
        '<li><a href="#tab3">Hardiness</a></li>\n'
        "</ul>\n"
        "<!--Start tab content-->\n"
        '<ul class="tabs-content">\n'
        '<li id="tab1" class="active">\n'
        f"{tab1} </li>\n"
        '<li id="tab2">\n'
        f"{tab2}\n"
        "</li>\n"
        '<li id="tab3">\n'
        f"{tab3}\n"
        "</li>\n"
        "</ul>"
    )


@app.route("/parse", methods=["POST"])
def parse():
    data = request.get_json() or {}
    text = data.get("text", "")
    tab1, tab2, tab3 = parse_tabs(text)
    if not tab1:
        return jsonify({"error": "Could not parse"}), 400
    return jsonify({"html": assemble_html(tab1, tab2, tab3)})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json() or {}
    plant_name = data.get("plant_name", "").strip()

    if not plant_name:
        return jsonify({"error": "Please enter a plant name."}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY is not set in .env file."}), 500

    client = anthropic.Anthropic(api_key=api_key)

    def event_stream():
        try:
            # Send immediate keepalive so Railway/proxies don't time out
            yield ": ping\n\n"
            full_text = ""
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": build_prompt(plant_name)}],
            ) as stream:
                for chunk in stream.text_stream:
                    full_text += chunk
                    yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

            tab1, tab2, tab3 = parse_tabs(full_text)
            if not tab1:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Could not parse response. Please try again.'})}\n\n"
                return

            html = assemble_html(tab1, tab2, tab3)
            yield f"data: {json.dumps({'type': 'done', 'html': html})}\n\n"

        except anthropic.APIError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Unexpected error: {str(e)}'})}\n\n"

    return Response(
        stream_with_context(event_stream()),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
