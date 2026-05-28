import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
from flask import Flask, request, jsonify, render_template, Response, stream_with_context
import anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ─────────────────────────────────────────────
#  KEYWORD ENGINE  (Google Search Console + Ubersuggest)
# ─────────────────────────────────────────────

_GSC_URL  = "https://docs.google.com/spreadsheets/d/1vkFJ2jeJRPVkzYvNeatrx2BskUIDjrMdmPjtkQHSYxE/export?format=csv&gid=2135342619"
_UBER_URL = "https://docs.google.com/spreadsheets/d/1vkFJ2jeJRPVkzYvNeatrx2BskUIDjrMdmPjtkQHSYxE/export?format=csv&gid=1941367263"
_kw_cache: dict = {}
_kw_cache_time: float = 0.0
_KW_TTL = 3600  # refresh every hour


def _fetch_csv(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        content = resp.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(content)))


def _load_keywords() -> dict:
    global _kw_cache, _kw_cache_time
    if _kw_cache and (time.time() - _kw_cache_time) < _KW_TTL:
        return _kw_cache
    gsc, uber = [], []
    try:
        gsc  = _fetch_csv(_GSC_URL)
        print(f"[KW] GSC loaded: {len(gsc)} rows", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[KW] GSC fetch error: {e}", file=sys.stderr, flush=True)
    try:
        uber = _fetch_csv(_UBER_URL)
        print(f"[KW] Uber loaded: {len(uber)} rows", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[KW] Uber fetch error: {e}", file=sys.stderr, flush=True)
    _kw_cache = {"gsc": gsc, "uber": uber}
    _kw_cache_time = time.time()
    return _kw_cache


def _plant_keywords_context(plant_name: str) -> str:
    """Return a keyword context block to inject into the Shopify prompt."""
    try:
        data = _load_keywords()
    except Exception:
        return ""

    plant_lower = plant_name.lower()
    # meaningful words (≥4 chars) from plant name for fuzzy matching
    plant_words = [w for w in plant_lower.split() if len(w) >= 4]

    def kw_matches(kw_text: str) -> bool:
        t = kw_text.lower()
        return plant_lower in t or any(w in t for w in plant_words)

    # ── GSC ──
    gsc_hits = []
    for row in data.get("gsc", []):
        kw       = row.get("Top queries", "").strip()
        plant_col = row.get("Plant Name", "").strip().lower()
        # match by keyword text OR Plant Name column
        if not kw:
            continue
        matched = kw_matches(kw) or (plant_lower in plant_col or
                  any(w in plant_col for w in plant_words))
        if not matched:
            continue
        try:
            clicks   = int(row.get("Clicks", 0) or 0)
            position = float(row.get("Position", 99) or 99)
            impr     = int(row.get("Impressions", 0) or 0)
            score    = clicks * (20.0 / max(position, 0.5))
            gsc_hits.append({"kw": kw, "clicks": clicks,
                              "impressions": impr, "position": round(position, 1),
                              "score": score})
        except Exception:
            pass
    gsc_hits.sort(key=lambda x: -x["score"])

    # ── Ubersuggest ──
    uber_hits = []
    for row in data.get("uber", []):
        kw = row.get("Keywords", "").strip()
        if not kw or not kw_matches(kw):
            continue
        try:
            volume = int(row.get("Volume", 0) or 0)
            diff   = int(row.get("Seo Difficulty", 99) or 99)
            score  = volume / max(diff, 1)
            uber_hits.append({"kw": kw, "volume": volume,
                               "difficulty": diff, "score": score})
        except Exception:
            pass
    uber_hits.sort(key=lambda x: -x["score"])

    if not gsc_hits and not uber_hits:
        return ""

    lines = ["SEO KEYWORD DATA for this plant (integrate naturally — no stuffing):"]
    if gsc_hits:
        lines.append("  Google Search Console (real buyer searches from succulentsbox.com):")
        for k in gsc_hits[:6]:
            lines.append(f'    "{k["kw"]}" — {k["clicks"]} clicks, pos {k["position"]}, {k["impressions"]:,} impressions')
    if uber_hits:
        lines.append("  Ubersuggest (search volume data):")
        for k in uber_hits[:5]:
            lines.append(f'    "{k["kw"]}" — volume {k["volume"]}, SEO difficulty {k["difficulty"]}')
    lines.append("  → Use the highest-click / highest-volume keywords in Title, Meta, and naturally in tab content.")

    return "\n".join(lines)


@app.route("/keywords-status")
def keywords_status():
    """Quick health-check: how many keywords are cached."""
    try:
        data = _load_keywords()
        return jsonify({
            "gsc_rows": len(data.get("gsc", [])),
            "uber_rows": len(data.get("uber", [])),
            "cache_age_sec": int(time.time() - _kw_cache_time),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


ICONS = {
    "cactus":      "https://cdn.shopify.com/s/files/1/2198/4603/files/Cactus_26a62e4c-2f57-4ecf-97de-fc345c4a381c_480x480.png?v=1600401339",
    "house":       "https://cdn.shopify.com/s/files/1/2198/4603/files/House_8db6c6e9-d5bb-466c-95af-bdbcbe9ff370_480x480.png?v=1600400127",
    "sun":         "https://cdn.shopify.com/s/files/1/2198/4603/files/Sun_65296975-26d8-49a3-83b6-b7b0ed15ae73_480x480.png?v=1600397323",
    "water":       "https://cdn.shopify.com/s/files/1/2198/4603/files/Water_d9b740b9-bfce-41c4-ae3e-16bf9678c77f_480x480.png?v=1600397177",
    "soil":        "https://cdn.shopify.com/s/files/1/2198/4603/files/Soil_40a149eb-9d43-4bf0-ac38-159c702c72f2_480x480.png?v=1600397158",
    "temperature": "https://cdn.shopify.com/s/files/1/2198/4603/files/Temperature_f0d54887-2130-48b9-9020-c9b8cdec1f12_480x480.png?v=1600399389",
    "fertilizer":  "https://cdn.shopify.com/s/files/1/2198/4603/files/Fertilizer_5853ff19-6010-410a-a082-3abe233cb8d2_480x480.png?v=1600399297",
    "spackle":     "https://cdn.shopify.com/s/files/1/2198/4603/files/Spackle_0b1c313b-e538-42e0-8818-7f0bdf63d530_480x480.png?v=1600397104",
    "growing":     "https://cdn.shopify.com/s/files/1/2198/4603/files/Growing_2961efa3-9828-496c-8a2c-fa57f0dd8914_480x480.png?v=1600399365",
}


def icon(name):
    return f'<img data-mce-fragment="1" width="16" height="16" data-src="{ICONS[name]}" class="lazyload" alt="">'


# Placeholder tokens Claude writes → replaced with actual <img> HTML after parsing
ICON_TOKENS = {
    "[[CACTUS]]":      "cactus",
    "[[HOUSE]]":       "house",
    "[[GROWING]]":     "growing",
    "[[SPACKLE]]":     "spackle",
    "[[SUN]]":         "sun",
    "[[WATER]]":       "water",
    "[[SOIL]]":        "soil",
    "[[TEMPERATURE]]": "temperature",
    "[[FERTILIZER]]":  "fertilizer",
}

def replace_icon_tokens(text: str) -> str:
    """Swap [[TOKEN]] placeholders for actual <img> icon HTML."""
    for token, name in ICON_TOKENS.items():
        text = text.replace(token, icon(name))
    return text


SYSTEM_PROMPT = """You are a professional plant product description writer for succulentsbox.com. Write accurate, engaging, SEO-optimized descriptions in English designed to rank well in AI-powered search (Google SGE, ChatGPT, Perplexity).

GLOBAL WRITING RULES (apply to all sections):
1. ANSWER REAL QUESTIONS — write as if directly answering what a buyer would type into Google or ask an AI assistant.
2. CONCISE — every sentence must add value. No filler, no padding.
3. NO EM DASHES — never use — or –. Use a comma, period, or rewrite.
4. NO BOLD in TAB1 or TAB2 — no **, <b>, or <strong> in Description or Care Guide.
5. SKIP IF NOT TRUE — only include features that genuinely apply to this plant. If the answer is "no" or "not applicable," omit it entirely.
6. PET SAFETY:
   - If the plant IS pet-safe: include naturally, e.g. "This plant is pet friendly."
   - If NOT pet-safe: use soft language only, e.g. "Cats and dogs should not be left to nibble this plant — keep it out of their reach." or "Best kept away from curious pets."
   - NEVER use the words: toxic, non-toxic, poisonous, ingestion hazard.
7. CHILD SAFETY:
   - If there are thorns, sharp edges, or irritating sap: mention gently, e.g. "Best placed out of reach of young children."
   - NEVER use the words: toxic, poisonous, dangerous, hazardous.
8. NO REPETITION — do not repeat information across paragraphs or tabs.

TITLE: 70-80 characters. Plant name first, then 1-2 of its most searchable functional features (e.g. "easy care," "air-purifying," "pet friendly," "drought tolerant," "low light"). No em dashes.

META: 160-200 characters exactly (count carefully — must be between 160 and 200).
  - Written for SELLING, not for plant care advice. Think: why would someone buy this as a gift or for their home?
  - Include the plant's main keyword (its name) + at least 1 secondary keyword (e.g. succulent, houseplant, indoor plant, air-purifying plant, easy care plant).
  - Front-load the most compelling value so AI assistants can extract it as a featured snippet.
  - Conversational tone, no keyword stuffing, no em dashes.
  Example structure: "Buy [Plant Name] — [key benefit]. [Secondary appeal]. [Call to feel/action]."

TAB1 — Description: 4 paragraphs in the existing HTML format (icon token + &nbsp;<span>text</span>).
Keep the same 4-paragraph structure. Within each paragraph, naturally weave in the relevant functional feature answers for this specific plant. ONLY include features that are TRUE — skip anything that does not apply.

  Para 1 [[CACTUS]] — Identity & Visual Style:
    Cover: common name, scientific name, plant family, origin region.
    Naturally include if true: visual style (architectural, trailing, colorful, minimalist), rare collector's find or beloved classic.

  Para 2 [[HOUSE]] — Placement & Environment:
    Cover: where it thrives best (room type, light level). Then naturally include whichever of these are TRUE:
    - Grows under office LEDs or low light (great for offices/apartments)
    - Tolerates dry heated-room air, or needs humidity (bathroom, kitchen)
    - Trailing plant suited for a hanging basket or high shelf
    - Suitable for balcony or outdoor placement
    - Sensitive to cold drafts or AC vents
    Skip anything that does not apply.

  Para 3 [[GROWING]] — Benefits & Safety:
    Naturally include whichever of these are TRUE for this plant:
    - Air purifying (filters formaldehyde or indoor pollutants)
    - Releases oxygen at night (ideal for bedroom)
    - Naturally humidifies dry air
    - Practical utility: healing gel (Aloe), culinary use (herbs), etc.
    - Low or no pollen, great for allergy-sensitive homes (only if true)
    Always include pet safety and child safety using the soft language rules:
      Pet-safe: "This plant is pet friendly."
      Not pet-safe: e.g. "Cats and dogs should not be left to nibble this plant — keep it out of their reach."
      Child safety if relevant: "Best placed out of reach of young children."

  Para 4 [[SPACKLE]] — Lifestyle & Shipping:
    Naturally include whichever of these are TRUE:
    - Beginner-friendly, hard to kill, forgiving of neglect
    - Survives 10+ days without watering (great for frequent travelers)
    - Fast grower or slow compact grower that keeps its shape
    - Propagates easily (great for sharing or expanding your collection)
    - Naturally pest-resistant, low-maintenance
    Always end with: ships bare-root without soil, carefully packaged for safe transit.

TAB2 — Care Guide (6 items, ONE sentence each):
  Item 1 Light, Item 2 Water, Item 3 Soil, Item 4 Temperature, Item 5 Fertilizer.
  Item 6 USDA Zones: ALWAYS lead with the indoor note first (e.g. "Best grown as a houseplant indoors across most of the US"), THEN state the zone range and 4-6 specific US states where it can grow outdoors year-round. Indoor first, outdoor second.

TAB3 — FAQs (exactly 6 Q&As, all inside one <p> tag):
  Questions must be the real buyer searches people type about THIS specific plant (not generic).
  Cover: most common care mistake, why leaves drop or change color, how to propagate, common pests, and 2 other topics specific to this species.
  Answers: 1-2 sentences, direct, mention the plant name or its specific trait. Bold Q lines with <strong>.

ICON TOKENS — use these exact tokens where indicated, do not invent others:
  TAB1: [[CACTUS]] [[HOUSE]] [[GROWING]] [[SPACKLE]]
  TAB2: [[SUN]] [[WATER]] [[SOIL]] [[TEMPERATURE]] [[FERTILIZER]] [[SPACKLE]]

OUTPUT — output ONLY these five sections, nothing before or after:

===TITLE===
[plain text, 70-80 chars]

===META===
[plain text, 160-200 chars]

===TAB1===
[[CACTUS]] &nbsp;<span>Para 1 text.</span><br><br>[[HOUSE]] &nbsp;<span>Para 2 text.</span><br><br>[[GROWING]] &nbsp;<span>Para 3 text.</span><br><br>[[SPACKLE]] &nbsp;<span>Para 4 text.</span>

===TAB2===
[[SUN]] &nbsp;<span>Light: one sentence.</span><br><br>[[WATER]] &nbsp;<span>Water: one sentence.</span><br><br>[[SOIL]] &nbsp;<span>Soil: one sentence.</span><br><br>[[TEMPERATURE]] &nbsp;<span>Temperature: one sentence.</span><br><br>[[FERTILIZER]] &nbsp;<span>Fertilizer: one sentence.</span><br><br>[[SPACKLE]] &nbsp;<span>USDA Zones: best grown as a houseplant indoors across most of the US; it thrives outdoors year-round in Zones [X]-[Y], including [State1], [State2], [State3], and [State4].</span>

===TAB3===
<p><strong>Q: question 1</strong><br>A: answer 1<br><br><strong>Q: question 2</strong><br>A: answer 2<br><br><strong>Q: question 3</strong><br>A: answer 3<br><br><strong>Q: question 4</strong><br>A: answer 4<br><br><strong>Q: question 5</strong><br>A: answer 5<br><br><strong>Q: question 6</strong><br>A: answer 6</p>"""


def build_prompt(plant_name: str) -> str:
    kw_context = _plant_keywords_context(plant_name)
    kw_block = f"\n\n{kw_context}" if kw_context else ""
    return f"""Plant: {plant_name}{kw_block}

Write all five sections for this plant following the OUTPUT format exactly.
- TITLE: 70-80 chars, plant name first, 1-2 functional features.
- META: 160-200 chars exactly. Sales-focused (why buy it, not how to care for it). Main keyword + 1 secondary keyword. Snippet-friendly.
- TAB1: 4 paragraphs, same HTML format (icon token + &nbsp;<span>text</span><br><br>). Weave in relevant functional features naturally — only those TRUE for this plant. Para 4 always ends with bare-root shipping note.
- TAB2: 6 items [[SUN]][[WATER]][[SOIL]][[TEMPERATURE]][[FERTILIZER]][[SPACKLE]], one sentence each. Last item: indoor note FIRST, then USDA zones + US states.
- TAB3: 6 Q&As in one <p> tag, bold <strong>Q:</strong> lines, plant-specific.
- No em dashes, no bold in TAB1/TAB2, soft pet/child safety language (no toxic/poisonous), skip anything not true.
- If SEO keyword data is provided, weave top keywords naturally into Title, Meta, and content."""


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
    tab3  = after("TAB3") or ""

    # Replace icon tokens with actual <img> HTML
    if tab1: tab1 = replace_icon_tokens(tab1)
    if tab2: tab2 = replace_icon_tokens(tab2)
    if tab3: tab3 = replace_icon_tokens(tab3)

    # Require at minimum: title, meta, tab1, tab2
    if title and meta and tab1 and tab2:
        print(f"[PARSE_OK] title={len(title)} tab3={'yes' if tab3 else 'empty'}", file=sys.stderr, flush=True)
        return title, meta, tab1, tab2, tab3

    print(f"[PARSE_FAILED] title={bool(title)} meta={bool(meta)} tab1={bool(tab1)} tab2={bool(tab2)}", file=sys.stderr, flush=True)
    print(f"[PARSE_FAILED] raw={text[:800]!r}", file=sys.stderr, flush=True)
    return None, None, None, None, None


def parse_tabs(text: str):
    """Legacy wrapper — returns (tab1, tab2, tab3) only."""
    _, _, tab1, tab2, tab3 = parse_all(text)
    return tab1, tab2, tab3


def assemble_html(tab1: str, tab2: str, tab3: str, plant_name: str = "") -> str:
    care_label = f"{plant_name} Care Guide" if plant_name else "Care Guide"
    return (
        '<ul class="tabs">\n'
        '<li><a href="#tab1" class="active">Description</a></li>\n'
        f'<li><a href="#tab2">{care_label}</a></li>\n'
        '<li><a href="#tab3">FAQs/Common Issues</a></li>\n'
        "</ul>\n"
        "<!--Start tab content-->\n"
        '<ul class="tabs-content">\n'
        '<li id="tab1" class="active">\n'
        f"{tab1}\n"
        "</li>\n"
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
    plant_name = data.get("plant_name", "")
    title, meta, tab1, tab2, tab3 = parse_all(text)
    if not tab1:
        return jsonify({"error": "Could not parse"}), 400
    return jsonify({"html": assemble_html(tab1, tab2, tab3, plant_name), "title": title or "", "meta": meta or ""})


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
                max_tokens=8192,
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

            html = assemble_html(tab1, tab2, tab3, plant_name)
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
