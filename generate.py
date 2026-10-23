"""
Ankur newsletter PDF generator.

Renders a PDF from stories (heading + note + photos) in a chosen visual
style — see styles.py for the style registry. "ankur-classic" matches the
real Ankur newsletter exactly: cream page, dark-green blocks, gold
headline, auto photo grid, white body text with just the main point of
each paragraph bolded (not the whole paragraph — mirrors how the real
newsletters read). Other styles reuse the same layout engine with
different colors/fonts/block shapes — see styles.py to add more.

Input: a list of stories, each (heading, note, photo_paths). See
stories/01-example-story/ for the on-disk format (heading.txt, note.txt,
photos/).

In note.txt: a blank line starts a new paragraph. Mark up at most the one
or two things that should stand out, same as the real newsletter does —
everything else should stay plain:
    ++Mr. Ashok Bildikar++   -> bold, highlight color (names, awards, dates)
    **word**                 -> bold, same color as the rest of the text
Leave a paragraph with no markup at all and its lead sentence is
auto-bolded, so nothing ever reads as a flat wall of text.

Usage:
    python generate.py [issue.json]

issue.json controls the cover/header text and chosen style:
    {
      "org_small": "ANKUR",
      "title_big": "NEWSLETTER",
      "date_range": "JANUARY - MARCH 2026",
      "style": "ankur-classic",
      "output_file": "output/newsletter.pdf"
    }
"""

import hashlib
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
)

from styles import DEFAULT_STYLE_ID, get_style

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".image_cache"

PAGE_W, PAGE_H = A4
MARGIN_L = 38
MARGIN_R = 38
MARGIN_BOTTOM = 65
INNER_TOP = 60

BLOCK_PAD = 14
PHOTO_GAP = 6
BLOCK_GAP = 16
COLUMN_GAP = 16


def _cover_top(style: dict) -> float:
    return style.get("cover_top", 150)


def _frame_heights(style: dict):
    cover_h = PAGE_H - _cover_top(style) - MARGIN_BOTTOM
    inner_h = PAGE_H - INNER_TOP - MARGIN_BOTTOM
    return cover_h, inner_h


def _is_fresh_page(avail_h: float, style: dict) -> bool:
    """True when avail_h is a full, empty frame (cover or inner page) rather
    than whatever's left over after earlier content on the same page."""
    cover_h, inner_h = _frame_heights(style)
    return abs(avail_h - cover_h) < 1.0 or abs(avail_h - inner_h) < 1.0


# --- text helpers -----------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "st", "prof", "sr", "jr", "vs", "etc",
    "inc", "ltd", "co", "no", "fig", "vol", "approx", "rev",
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*|\+\+(.+?)\+\+")


def _render_markup(text: str, highlight_hex: str) -> str:
    """Render manual markup, escaping everything else:
        **word**  -> bold, same color as the rest of the body
        ++word++  -> bold, highlight color (the one name/fact that should pop)
    """
    out = []
    pos = 0
    for m in _MARKUP_RE.finditer(text):
        out.append(_escape(text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(f"<b>{_escape(m.group(1))}</b>")
        else:
            out.append(f'<b><font color="{highlight_hex}">{_escape(m.group(2))}</font></b>')
        pos = m.end()
    out.append(_escape(text[pos:]))
    return "".join(out)


def _lead_sentence(text: str) -> str:
    """The first real sentence, skipping false ends after 'Mr.' / initials."""
    for m in _SENTENCE_END_RE.finditer(text):
        before = text[: m.start()]
        word = re.search(r"(\w+)$", before)
        w = word.group(1).lower() if word else ""
        if len(w) <= 1 or w in _ABBREVIATIONS:
            continue
        return text[: m.end()]
    return text


def format_article(text: str, highlight_hex: str) -> str:
    """Render a story's note as an article.

    If the note has manual ++highlight++ or **bold** markup, that's
    rendered as-is (this is how you match the real newsletter's look of
    highlighting one specific name/fact). Otherwise, the main point (lead
    sentence) of each paragraph is auto-bolded as a consistent fallback so
    nothing ever renders as a flat, unhighlighted wall of text.
    """
    text = text.strip()
    if not text:
        return ""
    paragraphs = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if "**" in para or "++" in para:
            paragraphs.append(_render_markup(para, highlight_hex).replace("\n", "<br/>"))
            continue
        lead = _lead_sentence(para)
        rest = para[len(lead):]
        paragraphs.append(f"<b>{_escape(lead)}</b>{_escape(rest)}".replace("\n", "<br/>"))
    return "<br/><br/>".join(paragraphs)


# --- photo grid ---------------------------------------------------------

MIN_PHOTO_CELL_W = 110  # below this, a column is too narrow for a 3-wide photo row


def max_photos_per_row(content_w: float) -> int:
    """How many photos fit per row before each one gets too small to read —
    matters once a style (e.g. a narrow newspaper column) makes content_w
    much smaller than a full-width block."""
    return max(1, min(3, int(content_w // MIN_PHOTO_CELL_W)))


def photo_rows(photos, per_row: int = 3):
    n = len(photos)
    if n == 0:
        return []
    if n <= per_row:
        return [photos]
    rows, i = [], 0
    while i < n:
        rows.append(photos[i:i + per_row])
        i += per_row
    # avoid a lonely single photo stranded on its own row, where possible
    if len(rows[-1]) == 1 and len(rows) > 1 and len(rows[-2]) > 1:
        rows[-1].insert(0, rows[-2].pop())
    return rows


def cropped_image_path(src: Path, target_w_pt: float, target_h_pt: float) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    out_w = max(1, int(target_w_pt / 72 * 200))
    out_h = max(1, int(target_h_pt / 72 * 200))
    key = hashlib.sha1(f"{src.resolve()}|{out_w}x{out_h}|{src.stat().st_mtime}".encode()).hexdigest()
    cached = CACHE_DIR / f"{key}.jpg"
    if cached.exists():
        return cached
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        fitted = ImageOps.fit(im, (out_w, out_h), Image.LANCZOS, centering=(0.5, 0.5))
        fitted.save(cached, "JPEG", quality=85)
    return cached


# --- story block ----------------------------------------------------------

class StoryBlock(Flowable):
    PHOTO_ASPECT = 0.66
    MAX_PHOTOS = 9
    MIN_SCALE = 0.55

    def __init__(self, heading, body, photo_paths, style: dict):
        super().__init__()
        self.heading_text = heading.strip()
        self.body_text = body.strip()
        self.photo_paths = list(photo_paths)[: self.MAX_PHOTOS]
        self.style = style
        self._geom = None

    def _build(self, avail_w, avail_h, scale):
        style = self.style
        pad = BLOCK_PAD if style["block_shape"] != "bordered" else BLOCK_PAD + 6
        content_w = avail_w - 2 * pad
        if style["block_shape"] == "bordered":
            content_w -= 8  # room for the left accent bar

        heading_style = ParagraphStyle(
            "Heading", fontName=style["heading_font"], fontSize=style.get("heading_font_size", 13.5) * scale,
            leading=style.get("heading_font_size", 13.5) * 1.2 * scale, textColor=style["heading_color"],
            alignment=style.get("heading_align", 1),
        )
        body_style = ParagraphStyle(
            "Body", fontName=style["body_font"], fontSize=10.3 * scale,
            leading=14.2 * scale, textColor=style["body_color"], alignment=0,
        )

        heading_flow = Paragraph(_escape(self.heading_text), heading_style) if self.heading_text else None
        heading_h = heading_flow.wrap(content_w, avail_h)[1] if heading_flow else 0

        rows = photo_rows(self.photo_paths, max_photos_per_row(content_w))
        row_geom = []
        photos_h = 0
        for row in rows:
            cols = len(row)
            cell_w = (content_w - PHOTO_GAP * (cols - 1)) / cols
            cell_h = cell_w * self.PHOTO_ASPECT * scale
            row_geom.append((row, cell_w, cell_h))
            photos_h += cell_h
        if rows:
            photos_h += PHOTO_GAP * (len(rows) - 1)

        body_markup = format_article(self.body_text, style["highlight_hex"])
        body_flow = Paragraph(body_markup, body_style) if body_markup else None
        body_h = body_flow.wrap(content_w, avail_h)[1] if body_flow else 0

        present = [p > 0 for p in (heading_h, photos_h, body_h)]
        gaps = max(0, sum(present) - 1)
        inner_gap = 10 * scale
        rule_allowance = 10 if style["block_shape"] == "divided" else 0
        total_h = 2 * pad + heading_h + photos_h + body_h + gaps * inner_gap + rule_allowance

        return dict(
            height=total_h, content_w=content_w, inner_gap=inner_gap, pad=pad,
            heading_flow=heading_flow, heading_h=heading_h,
            row_geom=row_geom, photos_h=photos_h,
            body_flow=body_flow, body_h=body_h, rule_allowance=rule_allowance,
        )

    def wrap(self, avail_w, avail_h):
        scale = 1.0
        g = self._build(avail_w, avail_h, scale)

        if g["height"] > avail_h and not _is_fresh_page(avail_h, self.style):
            # Doesn't fit in whatever's left on a page that's already partly
            # full. Report the true (oversized) height, unshrunk — reportlab
            # reads that as "doesn't fit" and pushes the whole block onto a
            # fresh page, where it gets the full frame height to lay out in.
            # Shrinking here instead would make blocks a random smaller size
            # purely because of where they happened to land on the page.
            self._geom = g
            self._avail_w = avail_w
            return (avail_w, g["height"])

        while g["height"] > avail_h and scale > self.MIN_SCALE:
            scale = max(self.MIN_SCALE, scale * 0.85)
            g = self._build(avail_w, avail_h, scale)

        # MIN_SCALE keeps text readable, but a genuinely overstuffed story
        # (many unrelated facts crammed into one block) can still be too
        # tall even on an empty page. Rather than crash the whole PDF build,
        # keep shrinking below the readable floor as a last resort — and
        # say so loudly, because a block that lands here needs to be split
        # into separate stories or trimmed, not just squeezed smaller.
        if g["height"] > avail_h:
            print(f"WARNING: story \"{self.heading_text or '(no heading)'}\" has far too much "
                  f"content for one block — shrinking past readable size to avoid a crash. "
                  f"Split it into separate stories or trim the text.")
            while g["height"] > avail_h and scale > 0.05:
                scale *= 0.85
                g = self._build(avail_w, avail_h, scale)

        self._geom = g
        self._avail_w = avail_w
        return (avail_w, g["height"])

    def draw(self):
        g = self._geom
        c = self.canv
        style = self.style
        w, h = self._avail_w, g["height"]
        pad = g["pad"]
        shape = style["block_shape"]

        x_offset = 0
        if shape == "filled":
            c.setFillColorRGB(*style["block_bg"])
            c.roundRect(0, 0, w, h, style.get("block_radius", 0), stroke=0, fill=1)
        elif shape == "bordered":
            c.setFillColorRGB(*style["block_bg"])
            c.setStrokeColorRGB(*style["ink"])
            c.setLineWidth(0.6)
            c.roundRect(0, 0, w, h, style.get("block_radius", 4), stroke=1, fill=1)
            c.setFillColorRGB(*style["accent_rgb"])
            c.rect(0, 0, 5, h, stroke=0, fill=1)
            x_offset = 8
        elif shape == "divided":
            c.setStrokeColorRGB(*style["ink"])
            c.setLineWidth(1.4)
            c.line(0, h - 2, w, h - 2)
            c.setLineWidth(0.5)
            c.line(0, h - 5, w, h - 5)

        y = h - pad - (g["rule_allowance"] if shape == "divided" else 0)
        text_x = pad + x_offset
        if g["heading_h"]:
            g["heading_flow"].drawOn(c, text_x, y - g["heading_h"])
            y -= g["heading_h"] + g["inner_gap"]

        if g["row_geom"]:
            for row, cell_w, cell_h in g["row_geom"]:
                x = text_x
                for photo in row:
                    try:
                        cached = cropped_image_path(photo, cell_w, cell_h)
                        c.drawImage(ImageReader(str(cached)), x, y - cell_h, width=cell_w, height=cell_h)
                    except Exception:
                        c.setFillColorRGB(0.8, 0.8, 0.8)
                        c.rect(x, y - cell_h, cell_w, cell_h, stroke=0, fill=1)
                    x += cell_w + PHOTO_GAP
                y -= cell_h + PHOTO_GAP
            y += PHOTO_GAP
            y -= g["inner_gap"]

        if g["body_h"]:
            g["body_flow"].drawOn(c, text_x, y - g["body_h"])


# --- page chrome ------------------------------------------------------

def _fill_background(c, style):
    c.setFillColorRGB(*style["cream"])
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)


def _draw_footer(c, doc, style):
    c.setLineWidth(1.4)
    c.setStrokeColorRGB(*style["ink"])
    c.line(MARGIN_L, 48, PAGE_W - MARGIN_R, 48)
    c.setFillColorRGB(*style["ink"])
    c.setFont(style["body_font_bold"], 10)
    c.drawString(MARGIN_L, 32, doc.org_small)


def _draw_masthead_bold_banner(c, doc, style):
    c.setFillColorRGB(*_hex_rgb(style["accent_hex"]))
    c.setFont(style["heading_font"], 16)
    c.drawString(MARGIN_L, PAGE_H - 48, doc.org_small)
    c.setFont(style["heading_font"], 46)
    c.drawString(MARGIN_L, PAGE_H - 100, doc.title_big)
    c.setFillColorRGB(*style["ink"])
    c.setFont(style["body_font_bold"], 14)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 125, doc.date_range)
    c.setLineWidth(2)
    c.setStrokeColorRGB(*style["ink"])
    c.line(MARGIN_L, PAGE_H - _cover_top(style) + 5, PAGE_W - MARGIN_R, PAGE_H - _cover_top(style) + 5)


def _draw_masthead_nameplate(c, doc, style):
    top = PAGE_H - 40
    c.setStrokeColorRGB(*style["ink"])
    c.setLineWidth(2.2)
    c.line(MARGIN_L, top, PAGE_W - MARGIN_R, top)
    c.setLineWidth(0.6)
    c.line(MARGIN_L, top - 4, PAGE_W - MARGIN_R, top - 4)

    c.setFillColorRGB(*style["ink"])
    c.setFont(style["body_font_bold"], 11)
    c.drawCentredString(PAGE_W / 2, top - 20, doc.org_small)

    c.setFont(style["heading_font"], 40)
    c.drawCentredString(PAGE_W / 2, top - 68, doc.title_big)

    c.setFont(style["body_font"], 12)
    c.drawCentredString(PAGE_W / 2, top - 88, doc.date_range)

    bottom = top - 100
    c.setLineWidth(0.6)
    c.line(MARGIN_L, bottom, PAGE_W - MARGIN_R, bottom)
    c.setLineWidth(2.2)
    c.line(MARGIN_L, bottom - 4, PAGE_W - MARGIN_R, bottom - 4)


def _draw_masthead_tab_underline(c, doc, style):
    accent = _hex_rgb(style["accent_hex"])
    c.setFillColorRGB(*accent)
    c.rect(MARGIN_L, PAGE_H - 36, 64, 16, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(style["body_font_bold"], 9)
    c.drawCentredString(MARGIN_L + 32, PAGE_H - 32, doc.org_small)

    c.setFillColorRGB(*style["ink"])
    c.setFont(style["heading_font"], 38)
    c.drawString(MARGIN_L, PAGE_H - 78, doc.title_big)

    c.setFillColorRGB(*accent)
    c.rect(MARGIN_L, PAGE_H - 92, 140, 4, stroke=0, fill=1)

    c.setFillColorRGB(*style["ink"])
    c.setFont(style["body_font"], 12)
    c.drawString(MARGIN_L, PAGE_H - 112, doc.date_range)


def _draw_masthead_minimal_rule(c, doc, style):
    c.setStrokeColorRGB(*style["ink"])
    c.setLineWidth(0.6)
    c.line(MARGIN_L, PAGE_H - 55, PAGE_W - MARGIN_R, PAGE_H - 55)

    c.setFillColorRGB(*style["ink"])
    c.setFont(style["body_font"], 10)
    c.drawString(MARGIN_L, PAGE_H - 70, doc.org_small)
    c.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 70, doc.date_range)

    c.setFont(style["heading_font"], 28)
    c.drawString(MARGIN_L, PAGE_H - 103, doc.title_big)


def _draw_masthead_boxed_formal(c, doc, style):
    box_top = PAGE_H - 30
    box_bottom = PAGE_H - _cover_top(style) + 18

    c.setStrokeColorRGB(*style["ink"])
    c.setLineWidth(1.2)
    c.rect(MARGIN_L, box_bottom, PAGE_W - MARGIN_L - MARGIN_R, box_top - box_bottom, stroke=1, fill=0)
    c.setLineWidth(0.4)
    c.rect(MARGIN_L + 4, box_bottom + 4, PAGE_W - MARGIN_L - MARGIN_R - 8, box_top - box_bottom - 8,
           stroke=1, fill=0)

    c.setFillColorRGB(*style["ink"])
    c.setFont(style["body_font_bold"], 10)
    c.drawCentredString(PAGE_W / 2, box_top - 24, doc.org_small)
    c.setFont(style["heading_font"], 30)
    c.drawCentredString(PAGE_W / 2, box_top - 58, doc.title_big)
    c.setFont(style["body_font"], 11)
    c.drawCentredString(PAGE_W / 2, box_top - 78, doc.date_range)


_MASTHEAD_DRAWERS = {
    "bold-banner": _draw_masthead_bold_banner,
    "nameplate": _draw_masthead_nameplate,
    "tab-underline": _draw_masthead_tab_underline,
    "minimal-rule": _draw_masthead_minimal_rule,
    "boxed-formal": _draw_masthead_boxed_formal,
}


def _hex_rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _draw_column_rules(c, style, top_margin):
    """A thin vertical rule between each pair of newspaper-style columns —
    drawn once per page as background, independent of story content."""
    n_cols = style.get("columns", 1)
    if n_cols <= 1:
        return
    total_w = PAGE_W - MARGIN_L - MARGIN_R
    col_w = (total_w - COLUMN_GAP * (n_cols - 1)) / n_cols
    c.setStrokeColorRGB(*style["ink"])
    c.setLineWidth(0.5)
    y_top = PAGE_H - top_margin
    for i in range(1, n_cols):
        x = MARGIN_L + i * col_w + (i - 0.5) * COLUMN_GAP
        c.line(x, MARGIN_BOTTOM, x, y_top)


def on_cover_page(c, doc):
    style = doc.style
    c.saveState()
    _fill_background(c, style)
    _MASTHEAD_DRAWERS.get(style["masthead_style"], _draw_masthead_bold_banner)(c, doc, style)
    _draw_column_rules(c, style, _cover_top(style))
    _draw_footer(c, doc, style)
    c.restoreState()


def on_inner_page(c, doc):
    style = doc.style
    c.saveState()
    _fill_background(c, style)
    c.setFillColorRGB(*style["ink"])
    c.setFont(style["body_font_bold"], 13)
    c.drawString(MARGIN_L, PAGE_H - 40, doc.date_range)
    c.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 40, doc.title_big)
    c.setLineWidth(2)
    c.line(MARGIN_L, PAGE_H - INNER_TOP + 8, PAGE_W - MARGIN_R, PAGE_H - INNER_TOP + 8)
    _draw_column_rules(c, style, INNER_TOP)
    _draw_footer(c, doc, style)
    c.restoreState()


def make_numbered_canvas(style: dict):
    """A Canvas subclass bound to this render's style, so the buffered
    'Page X of N' footer can be drawn in the right ink color/font."""

    class _NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFillColorRGB(*style["ink"])
                self.setFont(style["body_font_bold"], 10)
                self.drawRightString(PAGE_W - MARGIN_R, 32, f"Page {self._pageNumber} of {total}")
                rl_canvas.Canvas.showPage(self)
            rl_canvas.Canvas.save(self)

    return _NumberedCanvas


# --- loading + building --------------------------------------------------

def load_stories(stories_dir: Path):
    """stories/01-xxx/{heading.txt, note.txt, photos/*.jpg} -> [(heading, note, [Path,...]), ...]"""
    stories = []
    for folder in sorted(p for p in stories_dir.iterdir() if p.is_dir()):
        heading_file = folder / "heading.txt"
        note_file = folder / "note.txt"
        photos_dir = folder / "photos"
        heading = heading_file.read_text(encoding="utf-8").strip() if heading_file.exists() else ""
        note = note_file.read_text(encoding="utf-8").strip() if note_file.exists() else ""
        photos = []
        if photos_dir.exists():
            exts = {".jpg", ".jpeg", ".png", ".webp"}
            photos = sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in exts)
        if heading or note or photos:
            stories.append((heading, note, photos))
    return stories


def find_bare_photo_stories(stories: list) -> list:
    """Every photo (or photo group) in the real newsletter is paired with an
    article explaining it — a block is never just bare photos. Return the
    headings of any story that breaks that rule (has photos but no note),
    so callers can warn instead of silently publishing an uncaptioned block.
    """
    return [heading or "(no heading)" for heading, note, photos in stories if photos and not note.strip()]


def render_pdf(config: dict, stories: list, output_path: Path) -> Path:
    """stories: list of (heading, note, photo_paths)."""
    bare = find_bare_photo_stories(stories)
    if bare:
        print("WARNING: these stories have photos but no article text — every photo needs a "
              "caption explaining it, write one in note.txt before this goes out:")
        for heading in bare:
            print(f"  - {heading}")

    style = get_style(config.get("style", DEFAULT_STYLE_ID))
    cover_top = _cover_top(style)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
        topMargin=cover_top, bottomMargin=MARGIN_BOTTOM,
    )
    doc.org_small = config.get("org_small", "")
    doc.title_big = config.get("title_big", "NEWSLETTER")
    doc.date_range = config.get("date_range", "")
    doc.style = style

    # Frame defaults to 6pt padding on every side, which would silently
    # shrink the usable area below the frame-height constants and break
    # the _is_fresh_page() comparison in StoryBlock.wrap() — zero it out
    # so the frame's real usable area matches those constants exactly.
    frame_kwargs = dict(leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def build_frames(top_margin, id_prefix):
        n_cols = style.get("columns", 1)
        frame_h = PAGE_H - top_margin - MARGIN_BOTTOM
        if n_cols <= 1:
            return [Frame(MARGIN_L, MARGIN_BOTTOM, PAGE_W - MARGIN_L - MARGIN_R, frame_h,
                           id=id_prefix, **frame_kwargs)]
        total_w = PAGE_W - MARGIN_L - MARGIN_R
        col_w = (total_w - COLUMN_GAP * (n_cols - 1)) / n_cols
        # reportlab flows a single flowable stream across frames in list
        # order, moving to the next frame when one fills and to a new page
        # once all frames on it are full — exactly newspaper column flow,
        # no custom balancing logic needed.
        return [Frame(MARGIN_L + i * (col_w + COLUMN_GAP), MARGIN_BOTTOM, col_w, frame_h,
                       id=f"{id_prefix}{i}", **frame_kwargs) for i in range(n_cols)]

    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=build_frames(cover_top, "cover"), onPage=on_cover_page),
        PageTemplate(id="Inner", frames=build_frames(INNER_TOP, "inner"), onPage=on_inner_page),
    ])

    flow = [NextPageTemplate("Inner")]
    for i, (heading, note, photos) in enumerate(stories):
        flow.append(StoryBlock(heading, note, photos, style))
        if i != len(stories) - 1:
            flow.append(Spacer(1, BLOCK_GAP))

    doc.build(flow, canvasmaker=make_numbered_canvas(style))
    return output_path


def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR / "issue.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    stories = load_stories(BASE_DIR / "stories")
    if not stories:
        print(f"No stories found in {BASE_DIR / 'stories'}. Add folders like "
              f"stories/01-my-story/ with heading.txt, note.txt, photos/.")
        return
    output_path = BASE_DIR / config.get("output_file", "output/newsletter.pdf")
    render_pdf(config, stories, output_path)
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()
