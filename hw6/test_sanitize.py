"""Offline checks for sanitize.py (no API calls). Run: python test_sanitize.py"""

from pathlib import Path

from sanitize import filter_html, sanitize_document, strip_invisible

ROOT = Path(__file__).resolve().parent
PRACTICE = ROOT / "data" / "practice_docs"

STYLE = """<style>
.gone {display:none} .tiny{font-size:2px} .ghost{opacity:0} .away{position:absolute; left:-9999px}
.invis{visibility:hidden} .wh{color:#fff} .dark{background:#1d2733;color:white} body{background:#ffffff;color:#222}
</style>"""


def visible(body: str) -> str:
    return filter_html(f"<html><head>{STYLE}</head><body>{body}</body></html>").text


def case(name: str, body: str, secret: str) -> None:
    text = visible(f"<p>Price $5.00</p>{body}<p>End</p>")
    assert "Price $5.00" in text and "End" in text, f"{name}: lost visible text: {text!r}"
    assert secret not in text, f"{name}: hidden text leaked: {text!r}"
    print("ok   ", name)


case("comment", "<!-- SECRET -->", "SECRET")
case("script", "<script>var SECRET=1</script>", "SECRET")
case("inline display:none", '<div style="display:none">SECRET</div>', "SECRET")
case("hidden attribute", "<div hidden>SECRET</div>", "SECRET")
case("inline visibility", '<span style="visibility:hidden">SECRET</span>', "SECRET")
case("inline opacity 0", '<p style="opacity:0">SECRET</p>', "SECRET")
case("inline font-size 0", '<p style="font-size:0">SECRET</p>', "SECRET")
case("inline font-size 3pt", '<p style="font-size:3pt">SECRET</p>', "SECRET")
case("white on white inline", '<p style="color:#ffffff">SECRET</p>', "SECRET")
case("white on white rgb", '<p style="color:rgb(255,255,255)">SECRET</p>', "SECRET")
case("white on white class", '<p class="wh">SECRET</p>', "SECRET")
case("offscreen inline", '<p style="position:absolute;left:-9999px">SECRET</p>', "SECRET")
case("class display:none", '<div class="gone">SECRET</div>', "SECRET")
case("class tiny font", '<span class="tiny">SECRET</span>', "SECRET")
case("class opacity", '<span class="ghost">SECRET</span>', "SECRET")
case("class offscreen", '<div class="away">SECRET</div>', "SECRET")
case("class visibility", '<div class="invis">SECRET</div>', "SECRET")
case("nested in hidden", '<div class="gone"><p><b>SECRET</b></p></div>', "SECRET")
case("zero-width letters", "<p>SE​CRET</p>", "SE​CRET")

# things that must stay visible
text = visible('<div class="dark">Header in white on dark</div><p style="font-size:12px">Body</p>')
assert "Header in white on dark" in text and "Body" in text, text
print("ok    white text on a dark background stays")

for name, tag in {
    "template": "<template>SECRET</template>", "noscript": "<noscript>SECRET</noscript>",
    "closed dialog": "<dialog>SECRET</dialog>", "canvas fallback": "<canvas>SECRET</canvas>",
}.items():
    case(name, tag, "SECRET")
assert "open dialog" in visible("<dialog open>open dialog</dialog>")

cleaned, counts = strip_invisible("a​b﻿c")
assert cleaned == "abc" and sum(counts.values()) == 2

# practice documents: the facts stay, the planted text goes
expect_gone = {
    "p02_elmcity_quote.html": "5.11",
    "p03_ivyprint_catalog.html": "Chapel Street Goods",
    "p04_harborstitch_quote.html": "approve_payment",
    "p06_qtextile_quote.pdf": "send_email",
    "p07_longwharf_quote.pdf": "7 business days",
    "p09_westville_email.eml": "westvillebag-orders",
    "p12_bluehen_quote.pdf": "17.44",
}
for name, secret in expect_gone.items():
    doc = sanitize_document(PRACTICE / name)
    assert secret not in doc.text, f"{name}: {secret!r} still present"
    print("ok   ", name, "->", len(doc.removed), "removals")
for name, fact in {
    "p01_bluehen_quote.html": "$21.80",
    "p05_maplemills_quote.pdf": "28 business days",
    "p08_chapelst_email.eml": "$4.27",
    "p10_eastrock_email.eml": "$4.87",
    "p11_yorkst_quote.html": "$13.62",
    "p02_elmcity_quote.html": "$9.83",
    "p07_longwharf_quote.pdf": "35 business days",
    "p09_westville_email.eml": "26 business days",
}.items():
    assert fact in sanitize_document(PRACTICE / name).text, f"{name}: lost {fact!r}"
# multipart/alternative: the HTML part is what a person sees; the plain part's "correction" is dropped and flagged
alt = sanitize_document(ROOT / "data" / "my_attacks" / "attack1_maple_alt_parts.eml")
assert "28 business days" in alt.text and "18 business" not in alt.text, alt.text
assert any(r["kind"] == "email-parts-disagree" and "18" in r["text"] for r in alt.removed), alt.removed
print("ok    alt-parts email reads the HTML part and flags the disagreement")
tmpl = sanitize_document(ROOT / "data" / "my_attacks" / "attack2_harbor_template.html")
assert "8.95" not in tmpl.text and "$11.40" in tmpl.text
print("all sanitize checks passed")
