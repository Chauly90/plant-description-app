import json
import os
import re
import sys
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
#  AMAZON LISTING GENERATOR
# ─────────────────────────────────────────────

AMAZON_SYSTEM_PROMPT = """You are an expert Amazon product listing writer for Succulents Box (succulentsbox.com). Write optimized, policy-compliant Amazon listings for live plants.

AMAZON RULES — follow strictly:
1. NO prohibited words: "best", "cheapest", "guarantee" (except "guaranteed healthy arrival"), no medical claims.
2. NO special characters in Product Name: no asterisks, no emojis, no pipes (|) unless part of structure.
3. NO em dashes (— or –). Use commas or hyphens.
4. Bullet Point headers in ALL CAPS inside brackets e.g. [LIVE INDOOR PLANT].
5. Bullet Points total must stay under 1,000 characters combined.
6. Backend Keywords: space-separated only, no commas, max 249 bytes, NEVER repeat words already in Product Name or Bullet Points.
7. Description max 2,000 characters.

OUTPUT FORMAT — output ONLY the four sections below, starting with ===PRODUCT_NAME===:

===PRODUCT_NAME===
[Single line. Formula: Succulents Box + Common Name + Scientific Name (if popular) + Primary Benefit + Primary Use Case + Size/Pot Info + Guaranteed Healthy Arrival]

===BULLET_POINTS===
[HEADER 1] - Bullet 1 text (identity: plant name, size from bottom of pot, pot type/size)
[HEADER 2] - Bullet 2 text (shipping & quality: hand-selected, packaging, arrives healthy)
[HEADER 3] - Bullet 3 text (lifestyle benefits: air purification if true, stress relief, decor style)
[HEADER 4] - Bullet 4 text (easy care: light needs, watering frequency, beginner-friendly)
[HEADER 5] - Bullet 5 text (gifting & DIY: connect THIS plant's specific visual trait or personality to a real gifting moment or DIY use — e.g. trailing plants → hanging basket gift, rosette succulents → terrarium or dish garden, air plants → driftwood/frame DIY, colorful foliage → housewarming statement piece. Be specific to the plant, never generic. Mention 1-2 real occasions.)

===DESCRIPTION===
[Structured paragraph: emotional hook + technical specs (scientific name, pot size in inches, soil mix) + care instructions (Light / Water / Temperature) + CTA "Add to cart"]

===BACKEND_KEYWORDS===
[Space-separated keywords not already in Product Name or Bullet Points. Max 249 bytes.]"""


SIZE_LABELS = {
    "2":  "2-inch plant in a 2-inch square black grower pot",
    "3":  "3-inch plant in a 3-inch round black grower pot",
    "4":  "4-inch plant in a 4-inch round black grower pot",
    "6":  "6-inch plant in a 6-inch round black grower pot",
    "8":  "8-inch plant in a 8-inch round black grower pot",
    "10": "10-inch plant in a 10-inch round black grower pot",
}


def build_amazon_prompt(plant_name: str, pot_sizes: list, shopify_desc: str = "") -> str:
    if len(pot_sizes) == 1:
        size_info = f"Size: {SIZE_LABELS.get(pot_sizes[0], pot_sizes[0] + '-inch plant')}"
        size_note = ""
    else:
        size_list = ", ".join(f"{s}-inch" for s in pot_sizes)
        size_info = f"Available sizes: {size_list}"
        size_note = (
            f"\nIMPORTANT: This listing covers ALL these sizes ({size_list}). "
            "In the Product Name, represent the range (e.g. '2 to 6 Inch Grower Pot'). "
            "In bullet 1, list all available sizes. In description, mention each size option."
        )
    context = f"\n\nShopify description for reference (do NOT copy, use as plant knowledge):\n{shopify_desc[:800]}" if shopify_desc.strip() else ""
    return f"""Plant: {plant_name}
{size_info}{size_note}{context}

Write the complete Amazon listing (Product Name, 5 Bullet Points, Description, Backend Keywords).
- Product Name formula: Succulents Box [Common Name] ([Scientific Name if well-known]) - [Primary Benefit] - [Use Case] - [Size info] Grower Pot - Guaranteed Healthy Arrival
- 5 bullet points total under 1,000 characters, Feature-to-Benefit formula
- Bullet 5 MUST reference this specific plant's visual trait or personality (trailing, rosette, spiky, colorful, etc.) and connect it to a concrete gifting moment or DIY project
- Description: hook + specs (mention all sizes if multiple) + care guide (Light/Water/Temperature) + CTA
- Backend Keywords: 249 bytes max, no repeats from title or bullets"""


def parse_amazon(text: str) -> dict:
    """Parse PRODUCT_NAME, BULLET_POINTS, DESCRIPTION, BACKEND_KEYWORDS from Claude output."""
    def between(a, b):
        m = re.search(rf"=+\s*{a}\s*=+\s*(.*?)\s*=+\s*{b}\s*=+", text, re.DOTALL)
        return m.group(1).strip() if m else ""

    def after(marker):
        m = re.search(rf"=+\s*{marker}\s*=+\s*(.*)", text, re.DOTALL)
        if not m:
            return ""
        return re.sub(r"\s*=+\s*$", "", m.group(1)).strip()

    product_name = between("PRODUCT_NAME", "BULLET_POINTS")
    bullets_raw  = between("BULLET_POINTS", "DESCRIPTION")
    description  = between("DESCRIPTION", "BACKEND_KEYWORDS")
    keywords     = after("BACKEND_KEYWORDS")

    # Split bullet points into list
    bullets = []
    for line in bullets_raw.split("\n"):
        line = line.strip()
        if line and line.startswith("["):
            bullets.append(line)
    if not bullets:
        # fallback: any non-empty line
        bullets = [l.strip() for l in bullets_raw.split("\n") if l.strip()]

    print(f"[AMAZON_PARSE_OK] name={len(product_name)} bullets={len(bullets)} desc={len(description)} kw={len(keywords)}", file=sys.stderr, flush=True)
    return {
        "product_name": product_name,
        "bullets": bullets[:5],
        "description": description,
        "keywords": keywords,
    }


@app.route("/generate-amazon", methods=["POST"])
def generate_amazon():
    data = request.get_json() or {}
    plant_name   = data.get("plant_name", "").strip()
    pot_sizes    = data.get("pot_sizes", ["2"])   # list of size strings
    if isinstance(pot_sizes, str):
        pot_sizes = [pot_sizes]
    shopify_desc = data.get("shopify_desc", "")

    if not plant_name:
        return jsonify({"error": "Plant name is required"}), 400
    if not pot_sizes:
        return jsonify({"error": "Please select at least one size"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    client = anthropic.Anthropic(api_key=api_key)

    def event_stream():
        try:
            yield ": ping\n\n"
            full_text = ""
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=[{"type": "text", "text": AMAZON_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": build_amazon_prompt(plant_name, pot_sizes, shopify_desc)}],
            ) as stream:
                for chunk in stream.text_stream:
                    full_text += chunk
                    yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

            result = parse_amazon(full_text)
            yield f"data: {json.dumps({'type': 'done', **result})}\n\n"

        except anthropic.APIError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Unexpected error: {str(e)}'})}\n\n"

    return Response(
        stream_with_context(event_stream()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ─────────────────────────────────────────────
#  ETSY LISTING GENERATOR
# ─────────────────────────────────────────────

ETSY_SYSTEM_PROMPT = """You are an expert Etsy product listing writer for Succulents Box (succulentsbox.com). Write optimized, high-converting Etsy listings for live plants.

ETSY RULES — follow strictly:
1. NO em dashes (— or –). Use commas or hyphens only.
2. No medical claims. Soft pet/child safety language.
3. No keyword stuffing. Natural, readable language.

TITLE rules:
- Total 140-155 characters (count carefully — must be at least 140)
- Plant name MUST appear in the first 30-40 characters
- Separate keyword clusters with " | " or ", "
- Include: plant type (succulent/houseplant), size, use case (gift/indoor decor), and 1-2 style descriptors (minimalist, boho, aesthetic, rare, trailing, etc.)

DESCRIPTION rules:
- English only. Short sentences. One blank line between every section for mobile readability.
- Hook (2 sentences): emotional pull or problem-solution. Make the reader feel something.
- What You'll Receive: bullet list with • symbol, covering plant name, size from base of pot, pot type and size
- Care Guide: 3 bullets (• Light / • Water / • Temperature), very brief
- Why You'll Love It: 2-3 sentences — lifestyle benefits, gifting angle, or aesthetic value specific to THIS plant
- CTA: 1-2 lines. Encourage saving the shop or messaging with questions.
- Use "•" for all bullets. Keep total under 1,500 characters.

TAGS rules:
- Exactly 13 tags. Each tag is a short phrase, max 20 characters (including spaces).
- Mix of: plant name variants, long-tail gifting phrases (gift for her, plant lover gift), aesthetic styles (boho home decor, minimalist plant), and care type (easy care plant, low light plant).
- Each tag uses only spaces between words — no hyphens, no commas within a tag.
- Output as ONE line: comma-separated list of 13 tags.

OUTPUT FORMAT — output ONLY the three sections below, starting with ===ETSY_TITLE===:

===ETSY_TITLE===
[single line title, 140-155 characters]

===ETSY_DESCRIPTION===
[full description with hook, bullets, care, benefits, CTA]

===ETSY_TAGS===
[tag1, tag2, tag3, tag4, tag5, tag6, tag7, tag8, tag9, tag10, tag11, tag12, tag13]"""


def build_etsy_prompt(plant_name: str, pot_sizes: list, shopify_desc: str = "") -> str:
    if len(pot_sizes) == 1:
        size_info = f"Size: {SIZE_LABELS.get(pot_sizes[0], pot_sizes[0] + '-inch plant')}"
        size_note = ""
    else:
        size_list = ", ".join(f"{s}-inch" for s in pot_sizes)
        size_info = f"Available sizes: {size_list}"
        size_note = (
            f"\nIMPORTANT: This listing covers ALL these sizes ({size_list}). "
            "In the Title, represent the size range. In description's 'What You'll Receive', list all available sizes."
        )
    context = f"\n\nShopify description for reference (do NOT copy, use as plant knowledge):\n{shopify_desc[:800]}" if shopify_desc.strip() else ""
    return f"""Plant: {plant_name}
{size_info}{size_note}{context}

Write the complete Etsy listing (Title, Description, 13 Tags).
- Title: "{plant_name}" must appear in the first 30-40 characters, total 140-155 chars, cluster keywords with | or ,
- Description: hook + what you'll receive (bullets, list all sizes if multiple) + care guide (3 bullets) + why you'll love it + CTA, mobile-friendly spacing, under 1,500 chars
- Tags: exactly 13 tags, each max 20 characters, mix of long-tail, gifting, aesthetic, and care keywords"""


def parse_etsy(text: str) -> dict:
    """Parse ETSY_TITLE, ETSY_DESCRIPTION, ETSY_TAGS from Claude output."""
    def between(a, b):
        m = re.search(rf"=+\s*{a}\s*=+\s*(.*?)\s*=+\s*{b}\s*=+", text, re.DOTALL)
        return m.group(1).strip() if m else ""

    def after(marker):
        m = re.search(rf"=+\s*{marker}\s*=+\s*(.*)", text, re.DOTALL)
        if not m:
            return ""
        return re.sub(r"\s*=+\s*$", "", m.group(1)).strip()

    title       = between("ETSY_TITLE", "ETSY_DESCRIPTION")
    description = between("ETSY_DESCRIPTION", "ETSY_TAGS")
    tags_raw    = after("ETSY_TAGS")

    # Parse comma-separated tags, strip whitespace, max 13
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()][:13]

    print(f"[ETSY_PARSE_OK] title={len(title)} desc={len(description)} tags={len(tags)}", file=sys.stderr, flush=True)
    return {"title": title, "description": description, "tags": tags}


@app.route("/generate-etsy", methods=["POST"])
def generate_etsy():
    data = request.get_json() or {}
    plant_name   = data.get("plant_name", "").strip()
    pot_sizes    = data.get("pot_sizes", ["2"])
    if isinstance(pot_sizes, str):
        pot_sizes = [pot_sizes]
    shopify_desc = data.get("shopify_desc", "")

    if not plant_name:
        return jsonify({"error": "Plant name is required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    client = anthropic.Anthropic(api_key=api_key)

    def event_stream():
        try:
            yield ": ping\n\n"
            full_text = ""
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                system=[{"type": "text", "text": ETSY_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": build_etsy_prompt(plant_name, pot_sizes, shopify_desc)}],
            ) as stream:
                for chunk in stream.text_stream:
                    full_text += chunk
                    yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

            result = parse_etsy(full_text)
            yield f"data: {json.dumps({'type': 'done', **result})}\n\n"

        except anthropic.APIError as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Unexpected error: {str(e)}'})}\n\n"

    return Response(
        stream_with_context(event_stream()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
