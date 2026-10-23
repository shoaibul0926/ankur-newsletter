"""
Newsletter style registry.

Each style is a self-contained set of colors, fonts, and a block "shape"
(filled color block vs. a rule-divided broadsheet column vs. a bordered
magazine card) that generate.py renders from. Adding a new style means
adding one entry here — generate.py's rendering code branches on
style["block_shape"], not on the style id, so new styles reuse the same
three drawing paths instead of each needing bespoke layout code.

Roadmap: 8 of a planned ~20 styles are built so far, a batch at a time:
ankur-classic, broadsheet-editorial, bold-magazine, tabloid-pop,
minimalist, formal-bulletin, pastel-community, sports-two-tone. Ideas
for the next batch: a photo-led collage style (needs a new block_shape
with a full-bleed hero image), a letterpress/kraft-paper look, a
two-column dense-text style, a holiday/festive style, a black-and-gold
gala style, a kids'-drawing-inspired playful style. Each is just a new
dict below plus, if it needs a new block_shape or masthead_style, one
more branch in StoryBlock.draw() / _MASTHEAD_DRAWERS.
"""

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_DIR = Path(r"C:\Windows\Fonts")

_registered = {}


def _register(name: str, filename: str) -> str:
    """Register a system TTF under `name`; return `name` if it worked, else
    a safe built-in fallback so a style still renders on a machine that
    doesn't have that font installed."""
    if name in _registered:
        return _registered[name]
    path = FONT_DIR / filename
    try:
        pdfmetrics.registerFont(TTFont(name, str(path)))
        _registered[name] = name
    except Exception:
        _registered[name] = "Times-Roman" if "Bold" not in name else "Times-Bold"
    return _registered[name]


def _register_family(family: str, regular: str, bold: str, italic: str | None = None) -> tuple:
    """Register normal+bold(+italic) weights of a font AND tell reportlab
    they're one family (registerFontFamily). Without this, a Paragraph's
    <b>...</b> tag silently renders in the *regular* weight for any custom
    TTF font -- it only auto-resolves bold for reportlab's built-in fonts
    (Helvetica, Times-Roman, ...). Returns (regular_name, bold_name).
    """
    reg = _register(f"{family}", regular)
    bld = _register(f"{family}-Bold", bold)
    ital = _register(f"{family}-Italic", italic) if italic else reg
    pdfmetrics.registerFontFamily(family, normal=reg, bold=bld, italic=ital, boldItalic=bld)
    return reg, bld


# Classic serif newspaper type (broadsheet style)
TIMES, TIMES_BOLD = _register_family("TimesNewRoman", "times.ttf", "timesbd.ttf", "timesi.ttf")
# Elegant serif for formal/official type (formal-bulletin style)
GEORGIA, GEORGIA_BOLD = _register_family("Georgia", "georgia.ttf", "georgiab.ttf", "georgiai.ttf")
# Geometric heavy sans for modern magazine headlines (bold-magazine style)
GOTHIC, GOTHIC_BOLD = _register_family("CenturyGothic", "GOTHIC.TTF", "GOTHICB.TTF", "GOTHICI.TTF")
# Warm humanist sans for a friendly, welcoming feel (pastel-community style)
CANDARA, CANDARA_BOLD = _register_family("Candara", "Candara.ttf", "Candarab.ttf", "Candarai.ttf")
# Clean neutral sans for a quiet, minimal feel (minimalist style)
SEGOE, SEGOE_BOLD = _register_family("SegoeUI", "segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf")
# Condensed bold for tight sports-page headlines (sports-two-tone style)
ARIALN, ARIALN_BOLD = _register_family("ArialNarrow", "ARIALN.TTF", "ARIALNB.TTF", "ARIALNI.TTF")
# Heavy display face for loud tabloid headlines only -- never body text,
# it's too condensed/dense to read in long runs (tabloid-pop style)
IMPACT = _register("Impact", "impact.ttf")


STYLES = {

    "ankur-classic": {
        "name": "Ankur Classic",
        "description": "The real Ankur newsletter's own look: solid green blocks, gold headline, sharp corners.",
        "cream": (245 / 255, 245 / 255, 232 / 255),
        "ink": (0.07, 0.07, 0.07),
        "block_shape": "filled",
        "block_bg": (46 / 255, 100 / 255, 23 / 255),
        "block_radius": 0,
        "heading_color": (255 / 255, 222 / 255, 88 / 255),
        "body_color": (1, 1, 1),
        "highlight_hex": "#FFDE58",
        "accent_hex": "#2E6417",
        "heading_font": "Helvetica-Bold",
        "body_font": "Helvetica",
        "body_font_bold": "Helvetica-Bold",
        "masthead_style": "bold-banner",
    },

    "broadsheet-editorial": {
        "name": "Broadsheet Editorial",
        "description": "A real newspaper page: two-column layout, serif type, column rules, no color blocks.",
        "cream": (253 / 255, 252 / 255, 248 / 255),
        "ink": (0.1, 0.1, 0.1),
        "block_shape": "divided",
        "block_radius": 0,
        "heading_color": (0.08, 0.08, 0.08),
        "body_color": (0.15, 0.15, 0.15),
        "highlight_hex": "#8A1F11",
        "accent_hex": "#8A1F11",
        "heading_font": TIMES_BOLD,
        "heading_font_size": 14,
        "heading_align": 0,
        "body_font": TIMES,
        "body_font_bold": TIMES_BOLD,
        "masthead_style": "nameplate",
        "columns": 2,
    },

    "bold-magazine": {
        "name": "Bold Magazine",
        "description": "Modern magazine spread: heavy sans headlines, a colored tab accent, cream cards.",
        "cream": (250 / 255, 248 / 255, 243 / 255),
        "ink": (0.12, 0.1, 0.08),
        "block_shape": "bordered",
        "block_bg": (1, 1, 1),
        "block_radius": 4,
        "accent_rgb": (0.78, 0.25, 0.1),
        "heading_color": (0.1, 0.08, 0.06),
        "body_color": (0.2, 0.18, 0.15),
        "highlight_hex": "#C7400F",
        "accent_hex": "#C7400F",
        "heading_font": GOTHIC_BOLD,
        "heading_font_size": 15,
        "body_font": GOTHIC,
        "body_font_bold": GOTHIC_BOLD,
        "masthead_style": "tab-underline",
    },

    "tabloid-pop": {
        "name": "Tabloid Pop",
        "description": "Punchy and loud: bold coral blocks, big condensed headlines, high energy.",
        "cream": (255 / 255, 251 / 255, 244 / 255),
        "ink": (0.08, 0.06, 0.05),
        "block_shape": "filled",
        "block_bg": (232 / 255, 74 / 255, 39 / 255),
        "block_radius": 0,
        "heading_color": (1, 1, 1),
        "body_color": (1, 1, 1),
        "highlight_hex": "#FFE066",
        "accent_hex": "#E84A27",
        "heading_font": IMPACT,
        "heading_font_size": 20,
        "body_font": "Helvetica",
        "body_font_bold": "Helvetica-Bold",
        "masthead_style": "tab-underline",
    },

    "minimalist": {
        "name": "Minimalist",
        "description": "Quiet and airy: a single thin rule, muted stone ink, lots of whitespace.",
        "cream": (252 / 255, 251 / 255, 249 / 255),
        "ink": (0.3, 0.29, 0.26),
        "block_shape": "divided",
        "block_radius": 0,
        "heading_color": (0.2, 0.19, 0.17),
        "body_color": (0.33, 0.32, 0.29),
        "highlight_hex": "#8C7A5C",
        "accent_hex": "#8C7A5C",
        "heading_font": SEGOE_BOLD,
        "heading_font_size": 13,
        "heading_align": 0,
        "body_font": SEGOE,
        "body_font_bold": SEGOE_BOLD,
        "masthead_style": "minimal-rule",
    },

    "formal-bulletin": {
        "name": "Formal Bulletin",
        "description": "Official and composed: navy border cards, Georgia serif, a boxed masthead.",
        "cream": (250 / 255, 250 / 255, 247 / 255),
        "ink": (0.1, 0.12, 0.18),
        "block_shape": "bordered",
        "block_bg": (1, 1, 1),
        "block_radius": 0,
        "accent_rgb": (0.08, 0.18, 0.38),
        "heading_color": (0.08, 0.14, 0.3),
        "body_color": (0.16, 0.18, 0.22),
        "highlight_hex": "#14275F",
        "accent_hex": "#14275F",
        "heading_font": GEORGIA_BOLD,
        "heading_font_size": 14,
        "heading_align": 0,
        "body_font": GEORGIA,
        "body_font_bold": GEORGIA_BOLD,
        "masthead_style": "boxed-formal",
    },

    "pastel-community": {
        "name": "Pastel Community",
        "description": "Soft and friendly: peach-bordered cards, rounded corners, a warm welcoming feel.",
        "cream": (253 / 255, 250 / 255, 246 / 255),
        "ink": (0.25, 0.2, 0.18),
        "block_shape": "bordered",
        "block_bg": (1, 1, 1),
        "block_radius": 14,
        "accent_rgb": (0.93, 0.62, 0.49),
        "heading_color": (0.72, 0.33, 0.2),
        "body_color": (0.3, 0.25, 0.22),
        "highlight_hex": "#D16A3F",
        "accent_hex": "#ED9E7D",
        "heading_font": CANDARA_BOLD,
        "heading_font_size": 14,
        "body_font": CANDARA,
        "body_font_bold": CANDARA_BOLD,
        "masthead_style": "tab-underline",
    },

    "sports-two-tone": {
        "name": "Sports Two-Tone",
        "description": "High-contrast navy and red: bold sans headlines built for match-day results.",
        "cream": (246 / 255, 247 / 255, 249 / 255),
        "ink": (0.06, 0.08, 0.12),
        "block_shape": "filled",
        "block_bg": (0.09, 0.14, 0.26),
        "block_radius": 0,
        "heading_color": (1, 1, 1),
        "body_color": (0.88, 0.9, 0.93),
        "highlight_hex": "#E8384F",
        "accent_hex": "#13203F",
        "heading_font": ARIALN_BOLD,
        "heading_font_size": 16,
        "heading_align": 0,
        "body_font": "Helvetica",
        "body_font_bold": "Helvetica-Bold",
        "masthead_style": "bold-banner",
    },

}

DEFAULT_STYLE_ID = "ankur-classic"


def get_style(style_id: str) -> dict:
    return STYLES.get(style_id, STYLES[DEFAULT_STYLE_ID])
