import json
import os
import re
import base64
import sys
from io import BytesIO
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

WRITING RULES — follow strictly for ALL sections:
1. CONCISE: Every sentence must add value. No filler.
2. NO EM DASHES: Never use — or –. Use commas or rewrite.
3. NO BOLD TEXT: Never use ** or <b> or <strong>.
4. SKIP IF NOT APPLICABLE: Omit features that are not true for this plant. No "no" answers.
5. PET SAFETY: If safe, write "This plant is pet friendly" naturally. If not safe, use soft language like "best placed away from curious cats and dogs." NEVER use: toxic, non-toxic, poisonous.
6. CHILD SAFETY: Use gentle phrasing like "place out of reach of young children." NEVER use: toxic, poisonous, dangerous, hazardous.
7. NO REPETITION: Do not repeat info across sections.

TITLE rules:
- 70-80 characters (count carefully, must be at least 70)
- Plant name must appear first in the title
- After the plant name, highlight 1-2 of this plant's most notable functional features (e.g., "easy care," "air-purifying," "pet friendly," "drought tolerant," "fast growing," "low light," "healing gel")
- Use natural, engaging language optimized for AI Search — answers the searcher's intent directly
- No em dashes, no bold, no keyword stuffing

META DESCRIPTION rules:
- 160-200 characters (count carefully, must be at least 160)
- Written from the buyer's perspective: why would someone want this plant? What problem does it solve or what joy does it bring?
- Include the plant's main keyword (name) and at least 1 secondary keyword (e.g., succulent, houseplant, indoor plant, low-maintenance, care guide)
- Front-load the most valuable information so AI assistants can extract it as a featured snippet
- Natural sentence flow, conversational tone, no keyword stuffing, no em dashes

TAB1 — Description: exactly 3 paragraphs, each on ONE topic:
- Para 1 (identity): common name, scientific name, nicknames, brief origin
- Para 2 (appearance): leaf/stem shape, color, texture, size, growth habit, best pot style
- Para 3 (benefits & home): practical uses (only if true), pet/child safety, where to place at home

TAB2 — Care: 5 paragraphs, one per topic. No overlap with TAB1.
- Light / Water / Soil / Temperature & Humidity / Fertilizer

TAB3 — USDA Hardiness: zone range, minimum temperature in °F and °C, outdoor vs indoor guidance, frost note.

OUTPUT FORMAT — output ONLY the five sections below, starting with ===TITLE===, nothing before or after:

===TITLE===
[title — plain text, 60-70 characters, no HTML]

===META===
[meta description — plain text, 150-160 characters, no HTML]

===TAB1===
Exactly 3 paragraphs separated by <br><br>
- Para 1 starts with: {icon('cactus')}
- Para 2 starts with: {icon('soil')}
- Para 3 starts with: {icon('house')}

===TAB2===
5 paragraphs separated by <br><br>, each starting with its icon:
{icon('sun')} {icon('water')} {icon('soil')} {icon('temperature')} {icon('fertilizer')}

===TAB3===
<p><img width="1024" height="887" data-src="//cdn.shopify.com/s/files/1/2198/4603/files/map-of-growing-zones-usa-17-01_1024x1024.jpg?v=1553694426" class="lazyload" alt=""></p>
<p>USDA Hardiness Zones [X]-[Y] | Minimum temperature: [°F] ([°C])</p>
<p>[Outdoor year-round suitability and frost/winter care note.]</p>"""


def build_prompt(plant_name: str) -> str:
    return f"""Plant: {plant_name}

Write all five sections (TITLE, META, TAB1, TAB2, TAB3) for this plant.
- Title: 70-80 characters, plant name first, then notable functional features, AI-search optimized
- Meta: 160-200 characters, buyer intent focus (why buy this plant?), snippet-friendly, no keyword stuffing
- Tabs: correct icons, paragraphs separated by <br><br>, accurate USDA zones
- No em dashes, no bold, soft pet/child safety language, skip inapplicable features"""


def parse_all(text: str):
    """Parse TITLE, META, TAB1, TAB2, TAB3 from Claude output."""
    import sys

    def between(marker_a, marker_b):
        m = re.search(rf"=+\s*{marker_a}\s*=+\s*(.*?)\s*=+\s*{marker_b}\s*=+", text, re.DOTALL)
        return m.group(1).strip() if m else None

    def after(marker):
        m = re.search(rf"=+\s*{marker}\s*=+\s*(.*)", text, re.DOTALL)
        if not m:
            return None
        return re.sub(r'\s*=+\s*$', '', m.group(1)).strip()

    title = between("TITLE", "META")
    meta  = between("META",  "TAB1")
    tab1  = between("TAB1",  "TAB2")
    tab2  = between("TAB2",  "TAB3")
    tab3  = after("TAB3")

    if all([title, meta, tab1, tab2, tab3]):
        print(f"[PARSE_OK] title={len(title)} meta={len(meta)}", file=sys.stderr, flush=True)
        return title, meta, tab1, tab2, tab3

    print(f"[PARSE_FAILED] text={text[:600]!r}", file=sys.stderr, flush=True)
    return None, None, None, None, None


def parse_tabs(text: str):
    """Legacy wrapper — returns (tab1, tab2, tab3) only."""
    _, _, tab1, tab2, tab3 = parse_all(text)
    return tab1, tab2, tab3


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
    title, meta, tab1, tab2, tab3 = parse_all(text)
    if not tab1:
        return jsonify({"error": "Could not parse"}), 400
    return jsonify({"html": assemble_html(tab1, tab2, tab3), "title": title or "", "meta": meta or ""})


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

            title, meta, tab1, tab2, tab3 = parse_all(full_text)
            if not tab1:
                yield f"data: {json.dumps({'type': 'error', 'message': 'Could not parse response. Please try again.'})}\n\n"
                return

            html = assemble_html(tab1, tab2, tab3)
            yield f"data: {json.dumps({'type': 'done', 'html': html, 'title': title or '', 'meta': meta or ''})}\n\n"

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


# ─────────────────────────────────────────────
#  IMAGE GENERATION
# ─────────────────────────────────────────────

app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload limit


def analyze_plant_image(client_ant, b64: str, media_type: str, plant_name: str) -> str:
    """Use Claude vision to get a concise visual description for DALL-E prompts."""
    msg = client_ant.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": (
                    f"Describe this {plant_name} plant in 1-2 sentences for an image generation prompt. "
                    "Focus on: leaf shape, color, texture, growth pattern, and distinctive visual features. "
                    "Be specific and visual. No filler sentences."
                )},
            ],
        }],
    )
    return msg.content[0].text.strip()


def build_image_prompts(plant_name: str, plant_desc: str, pot_desc: str):
    """Return list of (label, prompt) for the 4 product photos."""
    base = f"realistic {plant_name} plant — {plant_desc}"
    return [
        ("Lifestyle", (
            f"High-quality lifestyle product catalog photo of a {base}, "
            "displayed in a beautiful white ceramic decorative pot on a white marble tabletop "
            "near a bright window with natural daylight. Elegant minimalist room interior, "
            "white and pastel theme, no red colors. The plant looks authentic and healthy. "
            "Soft natural shadows. Professional home-decor photography."
        )),
        ("Front View", (
            f"Professional product photo of a {base} in a {pot_desc}, "
            "photographed straight-on from the front against a pure white background. "
            "Natural authentic leaf shape and texture. Clean even studio lighting, "
            "no shadows, isolated on white. Standard e-commerce product photo."
        )),
        ("Top Down", (
            f"Professional product photo of a {base} shot from directly above "
            "(bird's-eye top-down view) against a pure white background. "
            "Full overhead view showing complete natural leaf arrangement, shape and veins. "
            "Clean even lighting, no shadows, isolated on white."
        )),
        ("Diagonal", (
            f"Professional product photo of a {base} shot from a 45-degree diagonal angle "
            "against a pure white background. Natural leaf shape, veins and texture clearly "
            "visible, full and lush. Clean even lighting, no shadows, isolated on white."
        )),
    ]


def generate_dalle_image(client_oai, prompt: str) -> str:
    """Generate one 1024×1024 image with DALL-E 3, resize to 1200×1200 JPEG, return data URL."""
    import requests as rq
    from PIL import Image

    resp = client_oai.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        quality="standard",
        n=1,
    )
    url = resp.data[0].url
    r = rq.get(url, timeout=60)
    r.raise_for_status()

    img = Image.open(BytesIO(r.content)).convert("RGB")
    img = img.resize((1200, 1200), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64str = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64str}"


def gen_alt_texts(client_ant, plant_name: str, plant_desc: str) -> list:
    """Generate 4 SEO alt texts via Claude."""
    msg = client_ant.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                f"Write 4 SEO alt texts for product images of {plant_name} ({plant_desc}).\n"
                "Formula: [Plant common name + scientific name if known] + [visual state/features] + [context/setting]\n"
                "Rules: under 125 characters each, natural language, no keyword stuffing, no em dashes, English.\n\n"
                "Output exactly 4 lines numbered 1-4:\n"
                "1. Lifestyle: plant in ceramic pot on marble table near window, bright room\n"
                "2. Front view: plant in black plastic pot, white background\n"
                "3. Top-down overhead view: white background\n"
                "4. Diagonal angle view: white background"
            ),
        }],
    )
    raw = msg.content[0].text
    alts = []
    for line in raw.split("\n"):
        line = line.strip()
        if line and line[0].isdigit() and (". " in line or "." in line):
            alts.append(re.sub(r"^\d+[\.\)]\s*", "", line))
    return alts


@app.route("/generate-images", methods=["POST"])
def generate_images_route():
    data = request.get_json() or {}
    plant_name = data.get("plant_name", "").strip()
    pot_size   = data.get("pot_size", "2")
    image_data = data.get("image_data", "")

    if not plant_name:
        return jsonify({"error": "Plant name is required"}), 400

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key    = os.environ.get("OPENAI_API_KEY")

    if not anthropic_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500
    if not openai_key:
        return jsonify({"error": "OPENAI_API_KEY not set. Add it to your .env file."}), 500

    def event_stream():
        try:
            yield ": ping\n\n"
            from openai import OpenAI
            client_ant = anthropic.Anthropic(api_key=anthropic_key)
            client_oai = OpenAI(api_key=openai_key)

            # 1. Analyze reference image with Claude (if uploaded)
            if image_data:
                yield f"data: {json.dumps({'type':'status','message':'Analyzing your plant photo...'})}\n\n"
                raw_b64    = image_data.split(",")[1] if "," in image_data else image_data
                media_type = image_data.split(";")[0].split(":")[1] if "data:" in image_data else "image/jpeg"
                plant_desc = analyze_plant_image(client_ant, raw_b64, media_type, plant_name)
            else:
                plant_desc = plant_name

            # 2. Generate alt texts
            yield f"data: {json.dumps({'type':'status','message':'Writing SEO alt texts...'})}\n\n"
            alt_texts = gen_alt_texts(client_ant, plant_name, plant_desc)

            # 3. Generate 4 images sequentially, stream each one
            pot_desc = "square black plastic nursery pot" if pot_size == "2" else "round black plastic nursery pot"
            prompts  = build_image_prompts(plant_name, plant_desc, pot_desc)

            labels_filename = {
                "Lifestyle":  "lifestyle",
                "Front View": "front-view",
                "Top Down":   "top-down",
                "Diagonal":   "diagonal",
            }

            for i, (label, prompt) in enumerate(prompts):
                yield f"data: {json.dumps({'type':'status','message':f'Generating {label} image ({i+1}/4)...'})}\n\n"
                img_data_url = generate_dalle_image(client_oai, prompt)
                alt  = alt_texts[i] if i < len(alt_texts) else ""
                fname = f"{plant_name.lower().replace(' ','-')}-{labels_filename[label]}.jpg"
                yield f"data: {json.dumps({'type':'image','index':i,'label':label,'data':img_data_url,'alt':alt,'filename':fname})}\n\n"

            yield f"data: {json.dumps({'type':'done'})}\n\n"

        except Exception as e:
            print(f"[IMG_ERROR] {e}", file=sys.stderr, flush=True)
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"

    return Response(
        stream_with_context(event_stream()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
