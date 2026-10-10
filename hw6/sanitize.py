"""Layer 1 (code): turn a vendor document into what a person actually sees.

Runs before the model. Returns the clean text plus a list of everything that was removed.

HTML   remove comments, script, style; remove any element hidden by inline styles OR by CSS
       classes/ids/tags defined in <style> blocks: display:none, the hidden attribute,
       visibility:hidden, opacity 0, font-size under 6px, text colored like its background,
       off-screen positioning, zero-size boxes that clip their content.
PDF    drop characters that are white, under 6pt, or outside the page box.
EML    read what a person sees: the HTML part, run through the HTML filter, when there is one
       (text/plain only when there is no HTML part); flag when the two parts disagree on a
       number; the email library decodes quoted-printable and base64.
All    strip zero-width and other invisible Unicode.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import pdfplumber
from bs4 import BeautifulSoup, Comment, Tag

MIN_FONT_PX = 6.0
OFFSCREEN_PX = -200.0
MIN_FONT_PT = 6.0
COLOR_TOLERANCE = 24  # sum of |dr|+|dg|+|db| on 0-255 channels
SNIPPET = 140
NON_RENDERED = ["script", "style", "noscript", "template", "dialog", "canvas", "audio", "video", "object", "embed", "iframe"]

NAMED_COLORS = {
    "white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "green": (0, 128, 0),
    "blue": (0, 0, 255), "yellow": (255, 255, 0), "gray": (128, 128, 128), "grey": (128, 128, 128),
    "silver": (192, 192, 192), "navy": (0, 0, 128), "ivory": (255, 255, 240), "snow": (255, 250, 250),
    "whitesmoke": (245, 245, 245), "ghostwhite": (248, 248, 255), "aliceblue": (240, 248, 255),
}
EXTRA_INVISIBLE = {0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFFA0}


@dataclass
class SanitizedDocument:
    text: str
    removed: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------- invisible unicode


def _is_invisible(ch: str) -> bool:
    cp = ord(ch)
    if ch in "\n\r\t":
        return False
    if cp in EXTRA_INVISIBLE or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return True
    return unicodedata.category(ch) in ("Cf", "Cc")


def strip_invisible(text: str) -> tuple[str, dict[str, int]]:
    """Remove zero-width, bidi, format and control characters. Returns (text, {U+XXXX: count})."""
    counts: dict[str, int] = {}
    kept: list[str] = []
    for ch in text:
        if _is_invisible(ch):
            key = f"U+{ord(ch):04X}"
            counts[key] = counts.get(key, 0) + 1
        elif ch in "  ":
            kept.append("\n")
        else:
            kept.append(ch)
    return "".join(kept), counts


def _invisible_record(counts: dict[str, int], where: str) -> list[dict[str, Any]]:
    if not counts:
        return []
    return [
        {
            "kind": "invisible-unicode",
            "where": where,
            "count": sum(counts.values()),
            "codepoints": counts,
        }
    ]


# ---------------------------------------------------------------- CSS helpers


def _parse_declarations(block: str) -> dict[str, str]:
    decls: dict[str, str] = {}
    for part in block.split(";"):
        if ":" not in part:
            continue
        name, value = part.split(":", 1)
        decls[name.strip().lower()] = re.sub(r"!important", "", value, flags=re.I).strip().lower()
    return decls


def _parse_stylesheet(css: str) -> list[tuple[str, dict[str, str]]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules: list[tuple[str, dict[str, str]]] = []
    for selectors, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = selectors.strip()
        if selectors.startswith("@"):
            continue
        decls = _parse_declarations(block)
        for selector in selectors.split(","):
            if selector.strip():
                rules.append((selector.strip(), decls))
    return rules


def _length_px(value: str) -> float | None:
    match = re.fullmatch(r"\s*(-?\d*\.?\d+)\s*(px|pt|em|rem|%|vw|vh|)\s*", value)
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2)
    if unit in ("px", ""):
        return number
    if unit == "pt":
        return number * 96 / 72
    if unit in ("em", "rem"):
        return number * 16
    if unit == "%":
        return number * 16 / 100
    return None


def _parse_color(value: str) -> tuple[int, int, int, float] | None:
    value = value.strip().lower()
    if value == "transparent":
        return (0, 0, 0, 0.0)
    if value in NAMED_COLORS:
        return (*NAMED_COLORS[value], 1.0)
    match = re.fullmatch(r"#([0-9a-f]{3,8})", value)
    if match:
        h = match.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) in (6, 8):
            alpha = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
        return None
    match = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if match:
        nums = [p.strip() for p in re.split(r"[,\s/]+", match.group(1).strip()) if p.strip()]
        try:
            rgb = [int(round(float(n[:-1]) * 2.55)) if n.endswith("%") else int(round(float(n))) for n in nums[:3]]
            alpha = float(nums[3][:-1]) / 100 if len(nums) > 3 and nums[3].endswith("%") else (float(nums[3]) if len(nums) > 3 else 1.0)
        except ValueError:
            return None
        if len(rgb) == 3:
            return (*rgb, alpha)
    return None


def _background_of(style: dict[str, str]) -> tuple[int, int, int, float] | None:
    for prop in ("background-color", "background"):
        if prop in style:
            for token in re.findall(r"#[0-9a-f]{3,8}|rgba?\([^)]*\)|[a-z]+", style[prop]):
                color = _parse_color(token)
                if color:
                    return color
    return None


def _hidden_reason(
    el: Tag, style: dict[str, str], ancestors_style: list[dict[str, str]], has_own_text: bool
) -> str | None:
    if el.has_attr("hidden"):
        return "hidden attribute"
    if style.get("display") == "none":
        return "display:none"
    if style.get("visibility") in ("hidden", "collapse"):
        return "visibility:hidden"
    if "opacity" in style:
        try:
            if float(style["opacity"].rstrip("%")) / (100 if style["opacity"].endswith("%") else 1) <= 0.01:
                return "opacity:0"
        except ValueError:
            pass
    if "font-size" in style:
        px = _length_px(style["font-size"])
        if px is not None and px < MIN_FONT_PX:
            return f"font-size under {MIN_FONT_PX:g}px ({style['font-size']})"
    for prop in ("left", "top", "right", "bottom", "margin-left", "margin-top", "text-indent"):
        if prop in style:
            px = _length_px(style[prop])
            if px is not None and px <= OFFSCREEN_PX:
                return f"off-screen positioning ({prop}:{style[prop]})"
    transform = style.get("transform", "")
    for number, unit in re.findall(r"translate[xy]?\(\s*(-?\d*\.?\d+)(px|pt|em|rem|%)", transform):
        px = _length_px(number + unit)
        if px is not None and px <= OFFSCREEN_PX:
            return f"off-screen positioning (transform:{transform})"
    if style.get("overflow") in ("hidden", "clip"):
        for prop in ("width", "height", "max-width", "max-height"):
            if prop in style:
                px = _length_px(style[prop])
                if px is not None and px <= 1:
                    return f"zero-size clipped box ({prop}:{style[prop]})"
    if not has_own_text:
        return None  # the colour check only applies to an element that carries text itself
    color = _parse_color(style["color"]) if "color" in style else None
    if color is None:
        for anc in reversed(ancestors_style):
            if "color" in anc:
                color = _parse_color(anc["color"])
                break
    if color is None:
        return None
    if color[3] <= 0.05:
        return "transparent text"
    background = _background_of(style)
    if background is None:
        for anc in reversed(ancestors_style):
            background = _background_of(anc)
            if background:
                break
    if background is None:
        background = (255, 255, 255, 1.0)  # a page is white unless something says otherwise
    if background[3] > 0.05 and sum(abs(a - b) for a, b in zip(color[:3], background[:3])) <= COLOR_TOLERANCE:
        return f"text color same as background (color {style.get('color') or 'inherited'})"
    return None


def _own_text(el: Tag) -> str:
    return "".join(str(c) for c in el.children if isinstance(c, str) and not isinstance(c, Comment)).strip()


def filter_html(html: str, where: str = "html") -> SanitizedDocument:
    soup = BeautifulSoup(html, "html.parser")
    removed: list[dict[str, Any]] = []

    css = "\n".join(s.get_text() for s in soup.find_all("style"))
    rules = _parse_stylesheet(css)

    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        if str(comment).strip():
            removed.append({"kind": "html-comment", "where": where, "text": str(comment).strip()[:SNIPPET]})
        comment.extract()
    # Elements a browser never renders as page text (or only as fallback when the real thing
    # fails): their content is not what a person sees.
    for tag in soup.find_all(NON_RENDERED):
        if tag.decomposed:
            continue
        if tag.name == "dialog" and tag.has_attr("open"):
            continue
        if tag.name != "style" and tag.get_text().strip():
            removed.append({"kind": f"html-{tag.name}", "where": where, "text": tag.get_text(" ", strip=True)[:SNIPPET]})
        tag.decompose()

    sheet_style: dict[int, dict[str, str]] = {}
    for selector, decls in rules:
        try:
            matches = soup.select(selector)
        except Exception:  # unsupported selector (pseudo-elements and the like)
            continue
        for el in matches:
            sheet_style.setdefault(id(el), {}).update(decls)

    def style_of(el: Tag) -> dict[str, str]:
        merged = dict(sheet_style.get(id(el), {}))
        merged.update(_parse_declarations(el.get("style", "") or ""))
        return merged

    for el in list(soup.find_all(True)):
        if el.decomposed:
            continue
        ancestors = [style_of(a) for a in el.parents if isinstance(a, Tag) and a.name != "[document]"]
        ancestors.reverse()
        style = style_of(el)
        reason = _hidden_reason(el, style, ancestors, bool(_own_text(el)))
        if reason:
            text = el.get_text(" ", strip=True)
            if text:
                removed.append({"kind": "html-hidden", "where": where, "reason": reason, "tag": el.name, "text": text[:SNIPPET]})
            el.decompose()

    text = soup.get_text(separator="\n", strip=True)
    text, counts = strip_invisible(text)
    removed.extend(_invisible_record(counts, where))
    return SanitizedDocument(text=text, removed=removed)


# ---------------------------------------------------------------- PDF


def _is_white(color: Any) -> bool:
    if color is None or isinstance(color, str):
        return False
    comps = [c for c in color if isinstance(c, (int, float))]
    if len(comps) in (1, 3):
        return all(c >= 0.95 for c in comps)
    if len(comps) == 4:  # CMYK: no ink at all
        return all(c <= 0.05 for c in comps)
    return False


def _pdf_char_problem(char: dict[str, Any], width: float, height: float) -> str | None:
    if _is_white(char.get("non_stroking_color")):
        return "white text"
    if char.get("size", 12) < MIN_FONT_PT:
        return f"text under {MIN_FONT_PT:g}pt"
    slack = 2.0
    if char["x1"] < -slack or char["x0"] > width + slack or char["bottom"] < -slack or char["top"] > height + slack:
        return "outside the page box"
    return None


def read_pdf(path: Path) -> SanitizedDocument:
    pages: list[str] = []
    removed: list[dict[str, Any]] = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            width, height = float(page.width), float(page.height)

            def visible(obj: dict[str, Any]) -> bool:
                return obj.get("object_type") != "char" or _pdf_char_problem(obj, width, height) is None

            def dropped(obj: dict[str, Any]) -> bool:
                return obj.get("object_type") == "char" and _pdf_char_problem(obj, width, height) is not None

            pages.append(page.filter(visible).extract_text() or "")
            bad = [c for c in page.chars if _pdf_char_problem(c, width, height)]
            if bad:
                hidden_text = (page.filter(dropped).extract_text() or "").strip()
                for reason in sorted({_pdf_char_problem(c, width, height) for c in bad}):
                    removed.append(
                        {
                            "kind": "pdf-hidden",
                            "where": f"page {number}",
                            "reason": reason,
                            "text": hidden_text[:SNIPPET],
                            "chars": sum(1 for c in bad if _pdf_char_problem(c, width, height) == reason),
                        }
                    )
    text, counts = strip_invisible("\n\n".join(pages))
    removed.extend(_invisible_record(counts, "pdf"))
    return SanitizedDocument(text=text, removed=removed)


# ---------------------------------------------------------------- email


_NUM = re.compile(r"(?<![\w-])\$?(\d[\d,]*(?:\.\d+)?)")


def _parts_disagree(plain_text: str, html_text: str) -> list[dict[str, Any]]:
    """Flag numbers that appear in one part of a multipart/alternative email but not the other."""
    plain_nums = {n.replace(",", "") for n in _NUM.findall(plain_text)}
    html_nums = {n.replace(",", "") for n in _NUM.findall(html_text)}
    only_plain, only_html = sorted(plain_nums - html_nums), sorted(html_nums - plain_nums)
    if not (only_plain or only_html):
        return []
    html_lines = {" ".join(line.split()).lower() for line in html_text.splitlines()}
    odd_lines = [
        " ".join(line.split())
        for line in plain_text.splitlines()
        if line.strip() and any(n in line for n in only_plain) and " ".join(line.split()).lower() not in html_lines
    ]
    return [
        {
            "kind": "email-parts-disagree",
            "where": "email",
            "text": f"numbers only in text/plain: {only_plain}; only in text/html: {only_html}",
            "plain_only_lines": [line[:SNIPPET] for line in odd_lines[:3]],
        }
    ]


def read_eml(path: Path) -> SanitizedDocument:
    message = BytesParser(policy=policy.default).parse(path.open("rb"))
    removed: list[dict[str, Any]] = []
    header_lines = [f"{h}: {message[h]}" for h in ("From", "To", "Cc", "Subject", "Date") if message[h]]

    plain: list[str] = []
    html_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind == "text/plain":
            plain.append(part.get_content())  # decodes quoted-printable / base64 and the charset
        elif kind == "text/html":
            html_parts.append(part.get_content())

    html_texts: list[str] = []
    for html in html_parts:
        filtered = filter_html(html, where="email text/html part")
        removed.extend(filtered.removed)
        html_texts.append(filtered.text)

    plain_text = "\n\n".join(p.strip() for p in plain if p.strip())
    html_text = "\n\n".join(t for t in html_texts if t.strip())
    if html_text:
        # A person reading the email in Mail sees the HTML part, so that is what the model gets.
        body = html_text
        if plain_text:
            removed.append(
                {"kind": "email-note", "where": "email", "text": "text/plain part not used: the HTML part is what a person sees"}
            )
            removed.extend(_parts_disagree(plain_text, html_text))
    else:
        body = plain_text
    text = "\n".join(header_lines) + "\n\n" + body
    text, counts = strip_invisible(text)
    removed.extend(_invisible_record(counts, "email"))
    return SanitizedDocument(text=text, removed=removed)


# ---------------------------------------------------------------- entry point


def sanitize_document(path: str | Path) -> SanitizedDocument:
    file = Path(path)
    suffix = file.suffix.lower()
    if suffix in (".html", ".htm"):
        return filter_html(file.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".pdf":
        return read_pdf(file)
    if suffix == ".eml":
        return read_eml(file)
    raise ValueError(f"Unsupported document type: {suffix!r} (expected .html, .pdf or .eml)")
