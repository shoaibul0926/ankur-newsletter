"""
Newsletter style registry.

Each style is a self-contained set of colors, fonts, and a block "shape"
(filled color block vs. a rule-divided broadsheet column vs. a bordered
magazine card) that generate.py renders from. Adding a new style means
adding one entry here — generate.py's rendering code branches on
style["block_shape"], not on the style id, so new styles reuse the same
three drawing paths instead of each needing bespoke layout code.

Roadmap: this ships 3 styles now (ankur-classic + two genuinely different
newspaper/magazine looks) out of a planned ~20, built a few at a time.
Easy next additions once more are wanted: a tabloid/collage style, a
formal black-tie bulletin, a pastel community-newsletter style, a
two-tone sports-page style, a minimalist Scandinavian style, etc. — each
is just a new dict below plus, if it needs a new block_shape, one more
branch in StoryBlock.draw().
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


# Serif family for the broadsheet style
GEORGIA = _register("Georgia", "georgia.ttf")
GEORGIA_BOLD = _register("Georgia-Bold", "georgiab.ttf")
TIMES = _register("TimesNewRoman", "times.ttf")
TIMES_BOLD = _register("TimesNewRoman-Bold", "timesbd.ttf")
TIMES_ITALIC = _register("TimesNewRoman-Italic", "timesi.ttf")
# Rounded sans for the magazine style
TREBUCHET = _register("Trebuchet", "trebuc.ttf")
TREBUCHET_BOLD = _register("Trebuchet-Bold", "trebucbd.ttf")


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
        "description": "Classic front-page newspaper: serif type, double rules, no color blocks.",
        "cream": (253 / 255, 252 / 255, 248 / 255),
        "ink": (0.1, 0.1, 0.1),
        "block_shape": "divided",
        "block_radius": 0,
        "heading_color": (0.08, 0.08, 0.08),
        "body_color": (0.15, 0.15, 0.15),
        "highlight_hex": "#8A1F11",
        "accent_hex": "#8A1F11",
        "heading_font": TIMES_BOLD,
        "heading_font_size": 16,
        "body_font": TIMES,
        "body_font_bold": TIMES_BOLD,
        "masthead_style": "nameplate",
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
        "heading_font": TREBUCHET_BOLD,
        "heading_font_size": 15,
        "body_font": TREBUCHET,
        "body_font_bold": TREBUCHET_BOLD,
        "masthead_style": "tab-underline",
    },

}

DEFAULT_STYLE_ID = "ankur-classic"


def get_style(style_id: str) -> dict:
    return STYLES.get(style_id, STYLES[DEFAULT_STYLE_ID])
