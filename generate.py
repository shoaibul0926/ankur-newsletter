"""
Ankur newsletter PDF generator — Phase 1.

Renders a PDF that matches the real Ankur newsletter template: cream page,
dark-green rounded story blocks, gold headline, auto photo grid, white body
text with just the main point of each paragraph bolded (not the whole
paragraph — mirrors how the real newsletters read).

Input: a list of stories, each (heading, note, photo_paths). See
stories/01-example-story/ for the on-disk format (heading.txt, note.txt,
photos/).

In note.txt: a blank line starts a new paragraph. Mark up at most the one
or two things that should stand out, same as the real newsletter does —
everything else should stay plain:
    ++Mr. Ashok Bildikar++   -> bold, gold highlight (names, awards, dates)
    **word**                 -> bold, same white as the rest of the text
Leave a paragraph with no markup at all and its lead sentence is
auto-bolded in white, so nothing ever reads as a flat wall of text.

Usage:
    python generate.py [issue.json]

issue.json controls the cover/header text:
    {
      "org_small": "ANKUR",
      "title_big": "NEWSLETTER",
      "date_range": "JANUARY - MARCH 2026",
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

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / ".image_cache"

PAGE_W, PAGE_H = A4
MARGIN_L = 38
MARGIN_R = 38
MARGIN_BOTTOM = 65
COVER_TOP = 150
INNER_TOP = 60

CREAM = (245 / 255, 245 / 255, 232 / 255)
GREEN = (46 / 255, 100 / 255, 23 / 255)   # matches the real newsletter's block color exactly
GOLD = (255 / 255, 222 / 255, 88 / 255)   # matches the real newsletter's headline gold exactly
WHITE = (1, 1, 1)
BLACK = (0.07, 0.07, 0.07)

BLOCK_PAD = 14
BLOCK_RADIUS = 0  # the real newsletter uses sharp, square-cornered blocks
PHOTO_GAP = 6
BLOCK_GAP = 16

COVER_FRAME_H = PAGE_H - COVER_TOP - MARGIN_BOTTOM
INNER_FRAME_H = PAGE_H - INNER_TOP - MARGIN_BOTTOM


def _is_fresh_page(avail_h: float) -> bool:
    """True when avail_h is a full, empty frame (cover or inner page) rather
    than whatever's left over after earlier content on the same page."""
    return abs(avail_h - COVER_FRAME_H) < 1.0 or abs(avail_h - INNER_FRAME_H) < 1.0

# --- text helpers -----------------------------------------------------

_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "st", "prof", "sr", "jr", "vs", "etc",
    "inc", "ltd", "co", "no", "fig", "vol", "approx", "rev",
}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


GOLD_HEX = "#FFDE58"
_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*|\+\+(.+?)\+\+")


def _render_markup(text: str) -> str:
    """Render manual markup, escaping everything else:
        **word**  -> bold white  (emphasis, same color as the rest of the body)
        ++word++  -> bold gold   (the one highlighted name/fact, like the real newsletter)
    """
    out = []
    pos = 0
    for m in _MARKUP_RE.finditer(text):
        out.append(_escape(text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(f"<b>{_escape(m.group(1))}</b>")
        else:
            out.append(f'<b><font color="{GOLD_HEX}">{_escape(m.group(2))}</font></b>')
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


def format_article(text: str) -> str:
    """Render a story's note as an article.

    If the note has manual ++gold highlight++ or **bold** markup, that's
    rendered as-is (this is how you match the real newsletter's look of
    highlighting one specific name/fact in gold). Otherwise, the main point
    (lead sentence) of each paragraph is auto-bolded in white as a
    consistent fallback so nothing ever renders as a flat, unhighlighted
    wall of text.
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
            paragraphs.append(_render_markup(para).replace("\n", "<br/>"))
            continue
        lead = _lead_sentence(para)
        rest = para[len(lead):]
        paragraphs.append(f"<b>{_escape(lead)}</b>{_escape(rest)}".replace("\n", "<br/>"))
    return "<br/><br/>".join(paragraphs)


# --- photo grid ---------------------------------------------------------

def photo_rows(photos):
    n = len(photos)
    if n == 0:
        return []
    if n <= 3:
        return [photos]
    if n == 4:
        return [photos[0:2], photos[2:4]]
    if n == 5:
        return [photos[0:3], photos[3:5]]
    rows, i = [], 0
    while i < n:
        rows.append(photos[i:i + 3])
        i += 3
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

    def __init__(self, heading, body, photo_paths):
        super().__init__()
        self.heading_text = heading.strip()
        self.body_text = body.strip()
        self.photo_paths = list(photo_paths)[: self.MAX_PHOTOS]
        self._geom = None

    def _build(self, avail_w, avail_h, scale):
        content_w = avail_w - 2 * BLOCK_PAD

        heading_style = ParagraphStyle(
            "Heading", fontName="Helvetica-Bold", fontSize=13.5 * scale,
            leading=16.5 * scale, textColor=GOLD, alignment=1,
        )
        body_style = ParagraphStyle(
            "Body", fontName="Helvetica", fontSize=10.3 * scale,
            leading=14.2 * scale, textColor=WHITE, alignment=0,
        )

        heading_flow = Paragraph(_escape(self.heading_text), heading_style) if self.heading_text else None
        heading_h = heading_flow.wrap(content_w, avail_h)[1] if heading_flow else 0

        rows = photo_rows(self.photo_paths)
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

        body_markup = format_article(self.body_text)
        body_flow = Paragraph(body_markup, body_style) if body_markup else None
        body_h = body_flow.wrap(content_w, avail_h)[1] if body_flow else 0

        present = [p > 0 for p in (heading_h, photos_h, body_h)]
        gaps = max(0, sum(present) - 1)
        inner_gap = 10 * scale
        total_h = 2 * BLOCK_PAD + heading_h + photos_h + body_h + gaps * inner_gap

        return dict(
            height=total_h, content_w=content_w, inner_gap=inner_gap,
            heading_flow=heading_flow, heading_h=heading_h,
            row_geom=row_geom, photos_h=photos_h,
            body_flow=body_flow, body_h=body_h,
        )

    def wrap(self, avail_w, avail_h):
        scale = 1.0
        g = self._build(avail_w, avail_h, scale)

        if g["height"] > avail_h and not _is_fresh_page(avail_h):
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
        w, h = self._avail_w, g["height"]

        c.setFillColorRGB(*GREEN)
        c.roundRect(0, 0, w, h, BLOCK_RADIUS, stroke=0, fill=1)

        y = h - BLOCK_PAD
        if g["heading_h"]:
            g["heading_flow"].drawOn(c, BLOCK_PAD, y - g["heading_h"])
            y -= g["heading_h"] + g["inner_gap"]

        if g["row_geom"]:
            for row, cell_w, cell_h in g["row_geom"]:
                x = BLOCK_PAD
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
            g["body_flow"].drawOn(c, BLOCK_PAD, y - g["body_h"])


# --- page chrome ------------------------------------------------------

def _fill_background(c):
    c.setFillColorRGB(*CREAM)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)


def _draw_footer(c, doc):
    c.setLineWidth(2)
    c.setStrokeColorRGB(*BLACK)
    c.line(MARGIN_L, 48, PAGE_W - MARGIN_R, 48)
    c.setFillColorRGB(*BLACK)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN_L, 32, doc.org_small)


def on_cover_page(c, doc):
    c.saveState()
    _fill_background(c)
    c.setFillColorRGB(*GREEN)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGIN_L, PAGE_H - 48, doc.org_small)
    c.setFont("Helvetica-Bold", 46)
    c.drawString(MARGIN_L, PAGE_H - 100, doc.title_big)
    c.setFillColorRGB(*BLACK)
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 125, doc.date_range)
    c.setLineWidth(2)
    c.setStrokeColorRGB(*BLACK)
    c.line(MARGIN_L, PAGE_H - COVER_TOP + 5, PAGE_W - MARGIN_R, PAGE_H - COVER_TOP + 5)
    _draw_footer(c, doc)
    c.restoreState()


def on_inner_page(c, doc):
    c.saveState()
    _fill_background(c)
    c.setFillColorRGB(*BLACK)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(MARGIN_L, PAGE_H - 40, doc.date_range)
    c.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 40, doc.title_big)
    c.setLineWidth(2)
    c.line(MARGIN_L, PAGE_H - INNER_TOP + 8, PAGE_W - MARGIN_R, PAGE_H - INNER_TOP + 8)
    _draw_footer(c, doc)
    c.restoreState()


class NumberedCanvas(rl_canvas.Canvas):
    """Buffers pages so the footer can print 'Page X of N' (N needs the final count)."""

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
            self.setFillColorRGB(*BLACK)
            self.setFont("Helvetica-Bold", 10)
            self.drawRightString(PAGE_W - MARGIN_R, 32, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


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

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=MARGIN_L, rightMargin=MARGIN_R,
        topMargin=COVER_TOP, bottomMargin=MARGIN_BOTTOM,
    )
    doc.org_small = config.get("org_small", "")
    doc.title_big = config.get("title_big", "NEWSLETTER")
    doc.date_range = config.get("date_range", "")

    # Frame defaults to 6pt padding on every side, which would silently
    # shrink the usable area below COVER_FRAME_H/INNER_FRAME_H and break the
    # _is_fresh_page() comparison in StoryBlock.wrap() — zero it out so the
    # frame's real usable area matches those constants exactly.
    frame_kwargs = dict(leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    cover_frame = Frame(MARGIN_L, MARGIN_BOTTOM, PAGE_W - MARGIN_L - MARGIN_R,
                         PAGE_H - COVER_TOP - MARGIN_BOTTOM, id="cover", **frame_kwargs)
    inner_frame = Frame(MARGIN_L, MARGIN_BOTTOM, PAGE_W - MARGIN_L - MARGIN_R,
                         PAGE_H - INNER_TOP - MARGIN_BOTTOM, id="inner", **frame_kwargs)

    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[cover_frame], onPage=on_cover_page),
        PageTemplate(id="Inner", frames=[inner_frame], onPage=on_inner_page),
    ])

    flow = [NextPageTemplate("Inner")]
    for i, (heading, note, photos) in enumerate(stories):
        flow.append(StoryBlock(heading, note, photos))
        if i != len(stories) - 1:
            flow.append(Spacer(1, BLOCK_GAP))

    doc.build(flow, canvasmaker=NumberedCanvas)
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
