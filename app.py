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


SYSTEM_PROMPT = f"""You are a professional plant product description writer for succulentsbox.com, an e-commerce plant store. Write accurate, engaging, SEO-friendly descriptions in English.

For every description, address these functional feature questions:

PLACEMENT & ENVIRONMENT:
- Light & Exposure: Needs direct sun or low-light shade? Thrives under office LEDs? Prone to scorching? Needs rotation?
- Temperature & Airflow: Handles drafty areas? Sensitive to AC/heater airflow? Needs temperature drop to bloom? Stay outdoors in autumn?
- Humidity & Moisture: Requires high humidity (bathroom)? Tolerates dry heated air? Suitable for terrarium? Thrives in kitchen steam?
- Space & Stability: Wide spreading or narrow vertical? Trailing plant needing high shelf? Sensitive to relocation? Deep roots needing large pot?
- Indoor vs. Outdoor: Strictly indoor or moves outside for summer? Withstands balcony wind and rain?

SAFETY & HEALTH:
- Pet Safety: Non-toxic if nibbled? Mild upset or emergency if ingested? Skin/paw irritation? Needs high shelf?
- Child Safety: Thorns, sharp edges, or berries hazardous for toddlers?
- Air Purification: Filters toxins like formaldehyde, benzene, carbon monoxide?
- Sleep Support: Releases oxygen at night (bedroom plant)?
- Allergy Check: Pollen or strong fragrance triggering sensitivities?
- Natural Humidity: Helps humidify dry spaces?

LIFESTYLE & MAINTENANCE:
- Travel Schedule: Survives 10-day trip, or needs frequent watering?
- Watering Routine: Shows thirst by wilting, or hides drought stress?
- Growth Speed: Slow keeper of shape, or fast-growing vine needing pruning?
- Pest Resistance: Naturally hardy, or a bug-attracting diva?
- Feeding Needs: Specialized fertilizer, or happy with standard soil?
- Utility: Healing (like Aloe), culinary (like herbs), or decorative only?

AESTHETICS & VALUE:
- Rarity: Common favorite or rare collector's find?
- Visual Style: Architectural modern lines, or soft bohemian trailing?
- Color Palette: Deep forest green, variegated white, or vivid color pops?
- Longevity: Seasonal splash, or an heirloom living decades?
- Potting Compatibility: Hanging basket, ceramic floor pot, or glass terrarium?
- Investment: Mature specimen or starter plant?

GENERAL:
- Toxic/poisonous status for pets and humans
- Essential care tips
- Propagation method
- Frost tolerance and USDA hardiness zones

OUTPUT — use EXACTLY these three section markers with no text before or after:

===TAB1===
3 paragraphs separated by <br><br>
- Para 1 starts with: {icon('cactus')} (what the plant is: common name, scientific name, nicknames, appeal, rarity)
- Para 2 starts with: {icon('house')} (appearance: leaf shape/color/texture, growth habit, native origin, potting style)
- Para 3 starts with: {icon('house')} (benefits: air purification, pet/child toxicity status, oxygen at night, feng shui, utility)

===TAB2===
5 paragraphs separated by <br><br>
- {icon('sun')} light requirements (direct/indirect/low light, scorching risk, office LED suitability, rotation)
- {icon('water')} watering (frequency, soil dryness test, drainage, drought signs, travel-friendly?)
- {icon('soil')} soil mix (ideal blend, amendments like perlite/peat/vermiculite)
- {icon('temperature')} temperature & humidity (ideal °F range, cold minimum, humidity needs, AC/heater sensitivity)
- {icon('fertilizer')} fertilizing (frequency, fertilizer type, growing vs. dormant season)

===TAB3===
<p><img width="1024" height="887" data-src="//cdn.shopify.com/s/files/1/2198/4603/files/map-of-growing-zones-usa-17-01_1024x1024.jpg?v=1553694426" class="lazyload" alt="">[USDA Zone range]</p>
<p>USDA Zone [zone]: to [°C] ([°F])</p>
(one <p> line per zone)"""


def build_prompt(plant_name: str) -> str:
    return f"""Plant: {plant_name}

Write the complete 3-tab product description following the system instructions exactly.
Use the correct icons per tab, separate paragraphs with <br><br>, and include accurate USDA zones for {plant_name}."""


def parse_tabs(text: str):
    t1 = re.search(r"===TAB1===\s*(.*?)\s*===TAB2===", text, re.DOTALL)
    t2 = re.search(r"===TAB2===\s*(.*?)\s*===TAB3===", text, re.DOTALL)
    t3 = re.search(r"===TAB3===\s*(.*?)(?:\s*===|$)", text, re.DOTALL)
    if not all([t1, t2, t3]):
        return None, None, None
    return t1.group(1).strip(), t2.group(1).strip(), t3.group(1).strip()


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
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
