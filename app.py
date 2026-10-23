"""
Ankur newsletter — Phase 3: local web UI.

Ties Phase 1 (generate.py) and Phase 2 (extract.py) together behind one
page: add a story by hand (heading + note + photos), or upload a deck
(.pptx/.pdf/.docx) to auto-extract several stories at once, review what's
queued, then generate the PDF. The stories/ folder on disk is the one
source of truth — this UI just reads and writes it, same as the CLI tools.

Run:
    python app.py

Then open http://127.0.0.1:5000
"""

import json
import re
import shutil
import uuid
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for

from extract import extract_deck, write_stories
from generate import BASE_DIR, find_bare_photo_stories, load_stories, render_pdf
from styles import DEFAULT_STYLE_ID, STYLES

app = Flask(__name__)
app.secret_key = "ankur-newsletter-builder-local-only"  # flash messages only; no auth, local tool

STORIES_DIR = BASE_DIR / "stories"
OUTPUT_DIR = BASE_DIR / "output"
UPLOAD_TMP = BASE_DIR / "web_uploads" / "tmp"
CONFIG_PATH = BASE_DIR / "issue.json"

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
DECK_EXTS = {".pptx", ".pdf", ".docx"}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        config = {"org_small": "ANKUR", "title_big": "NEWSLETTER", "date_range": "", "output_file": "output/newsletter.pdf"}
    config.setdefault("style", DEFAULT_STYLE_ID)
    return config


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def list_story_cards():
    cards = []
    for folder in sorted(p for p in STORIES_DIR.iterdir() if p.is_dir()):
        heading_file = folder / "heading.txt"
        note_file = folder / "note.txt"
        photos_dir = folder / "photos"
        photos = sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in PHOTO_EXTS) if photos_dir.exists() else []
        cards.append({
            "name": folder.name,
            "heading": heading_file.read_text(encoding="utf-8").strip() if heading_file.exists() else "",
            "note": note_file.read_text(encoding="utf-8").strip() if note_file.exists() else "",
            "photo_urls": [url_for("photo", relpath=f"{folder.name}/photos/{p.name}") for p in photos],
        })
    return cards


@app.route("/")
def index():
    stories = load_stories(STORIES_DIR)
    bare_photo_headings = find_bare_photo_stories(stories)
    cards = list_story_cards()
    total_photos = sum(len(c["photo_urls"]) for c in cards)
    return render_template("index.html", config=load_config(), stories=cards,
                            bare_photo_headings=bare_photo_headings, total_photos=total_photos,
                            available_styles=STYLES)


@app.route("/photo/<path:relpath>")
def photo(relpath):
    full = (STORIES_DIR / relpath).resolve()
    if not full.is_relative_to(STORIES_DIR.resolve()) or not full.exists():
        abort(404)
    return send_file(full)


@app.route("/config", methods=["POST"])
def update_config():
    config = load_config()
    config["org_small"] = request.form.get("org_small", "").strip() or "ANKUR"
    config["title_big"] = request.form.get("title_big", "").strip() or "NEWSLETTER"
    config["date_range"] = request.form.get("date_range", "").strip()
    style_id = request.form.get("style", "")
    config["style"] = style_id if style_id in STYLES else DEFAULT_STYLE_ID
    save_config(config)
    flash("Masthead saved.", "success")
    return redirect(url_for("index"))


@app.route("/stories/add", methods=["POST"])
def add_story():
    heading = request.form.get("heading", "").strip()
    note = request.form.get("note", "").strip()

    session_dir = UPLOAD_TMP / uuid.uuid4().hex[:10]
    images = []
    for i, f in enumerate(request.files.getlist("photos")):
        if f and f.filename:
            ext = Path(f.filename).suffix.lower()
            if ext not in PHOTO_EXTS:
                continue
            session_dir.mkdir(parents=True, exist_ok=True)
            dest = session_dir / f"photo_{i}{ext}"
            f.save(dest)
            images.append(dest)

    if images and not note:
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
        flash("Every photo needs an article explaining it — add a note before submitting.", "error")
        return redirect(url_for("index"))

    if heading or note or images:
        write_stories([{"heading": heading, "note": note, "images": images}], STORIES_DIR, "story")
        flash(f"Added “{heading or '(no heading)'}”.", "success")
    else:
        flash("Nothing to add — fill in a heading, note, or photo first.", "error")

    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)

    return redirect(url_for("index"))


@app.route("/stories/upload", methods=["POST"])
def upload_deck():
    f = request.files.get("deck")
    if not f or not f.filename:
        flash("No file uploaded.", "error")
        return redirect(url_for("index"))
    ext = Path(f.filename).suffix.lower()
    if ext not in DECK_EXTS:
        flash("Please upload a .pptx, .pdf, or .docx file.", "error")
        return redirect(url_for("index"))

    session_dir = UPLOAD_TMP / uuid.uuid4().hex[:10]
    session_dir.mkdir(parents=True, exist_ok=True)
    src_path = session_dir / f"source{ext}"
    f.save(src_path)

    try:
        slides = extract_deck(src_path, session_dir / "images")
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        flash(f"Could not read that file: {e}", "error")
        return redirect(url_for("index"))

    if slides:
        write_stories(slides, STORIES_DIR, Path(f.filename).stem)
        flash(f"Extracted {len(slides)} stor{'y' if len(slides) == 1 else 'ies'} from {f.filename}. "
              f"Review each note before generating.", "success")
    else:
        flash(f"No slides/pages with text or photos were found in {f.filename}.", "error")

    shutil.rmtree(session_dir, ignore_errors=True)
    return redirect(url_for("index"))


@app.route("/stories/<name>/delete", methods=["POST"])
def delete_story(name):
    if not re.match(r"^[\w\-]+$", name):
        abort(400)
    folder = (STORIES_DIR / name).resolve()
    if folder.is_relative_to(STORIES_DIR.resolve()) and folder.is_dir():
        shutil.rmtree(folder)
        flash("Story removed.", "success")
    return redirect(url_for("index"))


@app.route("/generate")
def generate():
    stories = load_stories(STORIES_DIR)
    if not stories:
        abort(400, "No stories yet — add one by hand or upload a deck first.")
    output_path = OUTPUT_DIR / "newsletter.pdf"
    render_pdf(load_config(), stories, output_path)
    return send_file(output_path, mimetype="application/pdf", as_attachment=False, download_name="newsletter.pdf")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
