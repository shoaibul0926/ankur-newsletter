"""
Ankur newsletter — Phase 3: local web UI.

Ties Phase 1 (generate.py) and Phase 2 (extract.py) together behind one
page: add a story by hand (heading + note + photos), or upload a deck
(.pptx/.pdf/.docx) to auto-extract several stories at once, review what's
queued, then generate the PDF.

Storage: locally (python app.py), the stories/ folder on disk is the one
source of truth, same as the CLI tools. On Vercel, the filesystem is
read-only outside /tmp, so github_storage.py is used instead -- every
read/write goes straight to the GitHub repo via its API, so changes show
up on the next page load with no redeploy needed. See github_storage.py.

Run:
    python app.py

Then open http://127.0.0.1:5000
"""

import io
import json
import mimetypes
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for

import github_storage as gh
from export_formats import render_docx, render_poster, render_website_html, write_blog_post
from extract import extract_deck, write_stories
from generate import BASE_DIR, load_stories, render_pdf
from styles import DEFAULT_STYLE_ID, STYLES

app = Flask(__name__)
app.secret_key = "ankur-newsletter-builder-local-only"  # flash messages only; no auth, local tool

STORIES_DIR = BASE_DIR / "stories"
OUTPUT_DIR = BASE_DIR / "output"
CONFIG_PATH = BASE_DIR / "issue.json"

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
DECK_EXTS = {".pptx", ".pdf", ".docx"}


def using_github() -> bool:
    return gh.is_enabled()


def load_config() -> dict:
    if using_github():
        config = gh.load_config()
    elif CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        config = {"org_small": "ANKUR", "title_big": "NEWSLETTER", "date_range": "", "output_file": "output/newsletter.pdf"}
    config.setdefault("style", DEFAULT_STYLE_ID)
    return config


def save_config(config: dict) -> None:
    if using_github():
        gh.save_config(config)
    else:
        CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def list_story_cards():
    """[{name, heading, note, photo_urls}, ...] -- the shape index.html renders."""
    if using_github():
        cards = []
        for s in gh.list_stories():
            cards.append({
                "name": s["name"],
                "heading": s["heading"],
                "note": s["note"],
                "photo_urls": [url_for("photo_gh", sha=sha, filename=fn) for fn, sha in s["photos"]],
            })
        return cards

    cards = []
    if not STORIES_DIR.exists():
        return cards
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


def current_stories_for_generate(tmp_dir: Path):
    """list of (heading, note, photo_paths) for render_pdf(). On GitHub
    storage, downloads each story's photos into tmp_dir first since the
    PDF renderer needs real files on disk."""
    if not using_github():
        return load_stories(STORIES_DIR)

    stories = []
    for s in gh.list_stories():
        photo_paths = []
        if s["photos"]:
            story_dir = tmp_dir / s["name"]
            story_dir.mkdir(parents=True, exist_ok=True)
            for filename, sha in s["photos"]:
                dest = story_dir / filename
                dest.write_bytes(gh.get_blob(sha))
                photo_paths.append(dest)
        if s["heading"] or s["note"] or photo_paths:
            stories.append((s["heading"], s["note"], photo_paths))
    return stories


def _story_by_name(name: str, tmp_dir: Path):
    """One story's full (heading, note, photo_paths) tuple by its folder/
    record name -- current_stories_for_generate() returns the whole list
    without names attached, so the poster route (which needs exactly one,
    picked from a dropdown) looks it up this way instead."""
    if not using_github():
        folder = STORIES_DIR / name
        if not folder.is_dir():
            return None
        heading_file, note_file, photos_dir = folder / "heading.txt", folder / "note.txt", folder / "photos"
        heading = heading_file.read_text(encoding="utf-8").strip() if heading_file.exists() else ""
        note = note_file.read_text(encoding="utf-8").strip() if note_file.exists() else ""
        photos = sorted(p for p in photos_dir.iterdir() if p.suffix.lower() in PHOTO_EXTS) if photos_dir.exists() else []
        return (heading, note, photos)

    for s in gh.list_stories():
        if s["name"] != name:
            continue
        photo_paths = []
        if s["photos"]:
            story_dir = tmp_dir / s["name"]
            story_dir.mkdir(parents=True, exist_ok=True)
            for filename, sha in s["photos"]:
                dest = story_dir / filename
                dest.write_bytes(gh.get_blob(sha))
                photo_paths.append(dest)
        return (s["heading"], s["note"], photo_paths)
    return None


@app.route("/")
def index():
    cards = list_story_cards()
    bare_photo_headings = [c["heading"] or "(no heading)" for c in cards if c["photo_urls"] and not c["note"]]
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


@app.route("/photo-gh/<sha>/<path:filename>")
def photo_gh(sha, filename):
    """Serves a story photo straight from its GitHub blob SHA -- used
    instead of /photo/... when running on github_storage."""
    try:
        data = gh.get_blob(sha)
    except Exception:
        abort(404)
    mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return send_file(io.BytesIO(data), mimetype=mimetype)


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

    uploads = []  # (filename, bytes) for GitHub, or (filename, Path) for local
    session_dir = None
    for i, f in enumerate(request.files.getlist("photos")):
        if f and f.filename:
            ext = Path(f.filename).suffix.lower()
            if ext not in PHOTO_EXTS:
                continue
            if using_github():
                uploads.append((f"img_{i}{ext}", f.read()))
            else:
                session_dir = session_dir or Path(tempfile.mkdtemp())
                dest = session_dir / f"photo_{i}{ext}"
                f.save(dest)
                uploads.append(dest)

    has_content = heading or note or uploads
    if has_content:
        if using_github():
            gh.add_stories([{"heading": heading, "note": note, "images": uploads}])
        else:
            write_stories([{"heading": heading, "note": note, "images": uploads}], STORIES_DIR, "story")
        if uploads and not note:
            flash(f"Added “{heading or '(no heading)'}” — but it has photos and no "
                  f"article yet. Write a caption for it before generating.", "error")
        else:
            flash(f"Added “{heading or '(no heading)'}”.", "success")
    else:
        flash("Nothing to add — fill in a heading, note, or photo first.", "error")

    if session_dir and session_dir.exists():
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

    session_dir = Path(tempfile.mkdtemp())
    src_path = session_dir / f"source{ext}"
    f.save(src_path)

    try:
        slides = extract_deck(src_path, session_dir / "images")
    except Exception as e:
        shutil.rmtree(session_dir, ignore_errors=True)
        flash(f"Could not read that file: {e}", "error")
        return redirect(url_for("index"))

    if slides:
        if using_github():
            gh_slides = [{"heading": s["heading"], "note": s["note"],
                          "images": [(p.name, p.read_bytes()) for p in s["images"]]} for s in slides]
            gh.add_stories(gh_slides, Path(f.filename).stem)
        else:
            write_stories(slides, STORIES_DIR, Path(f.filename).stem)
        no_article = [s["heading"] or "(no heading)" for s in slides if s["images"] and not s["note"]]
        if no_article:
            flash(f"Extracted {len(slides)} stor{'y' if len(slides) == 1 else 'ies'} from {f.filename} — "
                  f"but {f.filename} has no article text for {len(no_article)} of them "
                  f"({', '.join(no_article[:3])}{', ...' if len(no_article) > 3 else ''}). "
                  f"Write captions for those before generating.", "error")
        else:
            flash(f"Extracted {len(slides)} stor{'y' if len(slides) == 1 else 'ies'} from {f.filename}. "
                  f"Review each note before generating.", "success")
    else:
        flash(f"{f.filename} doesn't have any article text or photos in it — nothing to extract.", "error")

    shutil.rmtree(session_dir, ignore_errors=True)
    return redirect(url_for("index"))


@app.route("/stories/<name>/delete", methods=["POST"])
def delete_story(name):
    if not re.match(r"^[\w\-]+$", name):
        abort(400)
    if using_github():
        gh.delete_story(name)
        flash("Story removed.", "success")
    else:
        folder = (STORIES_DIR / name).resolve()
        if folder.is_relative_to(STORIES_DIR.resolve()) and folder.is_dir():
            shutil.rmtree(folder)
            flash("Story removed.", "success")
    return redirect(url_for("index"))


@app.route("/generate")
def generate():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        stories = current_stories_for_generate(tmp_dir)
        if not stories:
            abort(400, "No stories yet — add one by hand or upload a deck first.")
        output_path = (Path(tempfile.gettempdir()) / f"newsletter_{uuid.uuid4().hex[:8]}.pdf"
                        if using_github() else OUTPUT_DIR / "newsletter.pdf")
        render_pdf(load_config(), stories, output_path)
        response = send_file(output_path, mimetype="application/pdf", as_attachment=False,
                              download_name="newsletter.pdf")
        # This same URL serves a freshly-regenerated file every time (e.g. after
        # switching styles) — without this, browsers/PDF viewers can silently
        # keep showing a cached response from an earlier style.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/generate-docx")
def generate_docx():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        stories = current_stories_for_generate(tmp_dir)
        if not stories:
            abort(400, "No stories yet — add one by hand or upload a deck first.")
        output_path = (Path(tempfile.gettempdir()) / f"newsletter_{uuid.uuid4().hex[:8]}.docx"
                        if using_github() else OUTPUT_DIR / "newsletter.docx")
        render_docx(load_config(), stories, output_path)
        return send_file(output_path, as_attachment=True, download_name="newsletter.docx")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/export-website")
def export_website():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        stories = current_stories_for_generate(tmp_dir)
        if not stories:
            abort(400, "No stories yet — add one by hand or upload a deck first.")
        output_path = Path(tempfile.gettempdir()) / f"newsletter_site_{uuid.uuid4().hex[:8]}.html"
        render_website_html(load_config(), stories, output_path)
        return send_file(output_path, as_attachment=True, download_name="newsletter.html", mimetype="text/html")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/blog-post")
def blog_post():
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        stories = current_stories_for_generate(tmp_dir)
        if not stories:
            abort(400, "No stories yet — add one by hand or upload a deck first.")
        output_path = Path(tempfile.gettempdir()) / f"newsletter_blog_{uuid.uuid4().hex[:8]}.md"
        write_blog_post(load_config(), stories, output_path)
        return send_file(output_path, as_attachment=True, download_name="blog-post.md", mimetype="text/markdown")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.route("/poster")
def poster():
    name = request.args.get("story", "")
    if not name or not re.match(r"^[\w\-]+$", name):
        abort(400, "Pick a story to make a poster from.")
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        story = _story_by_name(name, tmp_dir)
        if story is None:
            abort(404, "That story wasn't found — it may have been removed.")
        output_path = Path(tempfile.gettempdir()) / f"poster_{uuid.uuid4().hex[:8]}.png"
        render_poster(load_config(), story, output_path)
        return send_file(output_path, as_attachment=True, download_name=f"poster-{name}.png", mimetype="image/png")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
