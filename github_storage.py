"""
GitHub-backed storage for the deployed (Vercel) copy of the app.

Vercel's filesystem is read-only outside /tmp, and /tmp isn't shared
across invocations -- so the deployed app can't use the local-disk
stories/ folder the way the CLI tools and local `python app.py` do.
Instead, when running on Vercel, every read/write goes straight to the
GitHub repo via the REST API: adding a story commits two-three small
files, deleting one removes them, and every page load reads the live
repo tree -- so changes show up immediately, no redeploy needed.

Enabled only when both VERCEL and GITHUB_TOKEN are set; app.py falls
back to the local filesystem otherwise (local dev, and the CLI tools
never touch this module at all).
"""

import base64
import json
import os
import re
import urllib.error
import urllib.request

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "shoaibul0926/ankur-newsletter")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def is_enabled() -> bool:
    return bool(os.environ.get("VERCEL")) and bool(GITHUB_TOKEN)


def _request(method: str, url: str, data: dict | None = None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} -> {e.code}: {detail}") from e


def _tree():
    """The full recursive tree of the repo at GITHUB_BRANCH -- one API
    call instead of walking stories/ directory by directory."""
    ref = _request("GET", f"{API_BASE}/git/ref/heads/{GITHUB_BRANCH}")
    commit_sha = ref["object"]["sha"]
    tree = _request("GET", f"{API_BASE}/git/trees/{commit_sha}?recursive=1")
    return tree.get("tree", [])


def get_blob(sha: str) -> bytes:
    blob = _request("GET", f"{API_BASE}/git/blobs/{sha}")
    return base64.b64decode(blob["content"])


def list_stories() -> list[dict]:
    """[{name, heading, note, photos: [(filename, sha), ...]}, ...] sorted by folder name."""
    entries = [e for e in _tree() if e["path"].startswith("stories/") and e["type"] == "blob"]
    folders: dict[str, dict] = {}
    for e in entries:
        parts = e["path"].split("/")  # stories/NN-slug/... or stories/NN-slug/photos/...
        if len(parts) < 3:
            continue
        folder = parts[1]
        rest = "/".join(parts[2:])
        folders.setdefault(folder, {"heading_sha": None, "note_sha": None, "photos": []})
        if rest == "heading.txt":
            folders[folder]["heading_sha"] = e["sha"]
        elif rest == "note.txt":
            folders[folder]["note_sha"] = e["sha"]
        elif rest.startswith("photos/"):
            filename = rest.split("/", 1)[1]
            if Path_suffix(filename) in PHOTO_EXTS:
                folders[folder]["photos"].append((filename, e["sha"]))

    results = []
    for name in sorted(folders):
        f = folders[name]
        heading = get_blob(f["heading_sha"]).decode("utf-8").strip() if f["heading_sha"] else ""
        note = get_blob(f["note_sha"]).decode("utf-8").strip() if f["note_sha"] else ""
        results.append({
            "name": name,
            "heading": heading,
            "note": note,
            "photos": sorted(f["photos"]),
        })
    return results


def Path_suffix(filename: str) -> str:
    i = filename.rfind(".")
    return filename[i:].lower() if i >= 0 else ""


def _next_index() -> int:
    existing = [e["path"].split("/")[1] for e in _tree()
                if e["path"].startswith("stories/") and len(e["path"].split("/")) >= 2]
    nums = [int(m.group(1)) for name in set(existing) if (m := re.match(r"^(\d+)-", name))]
    return (max(nums) + 1) if nums else 1


def _slugify(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or fallback).lower()).strip("-")[:40]
    return slug or "story"


def _put_file(path: str, content_bytes: bytes, message: str):
    _request("PUT", f"{API_BASE}/contents/{path}", {
        "message": message,
        "content": base64.b64encode(content_bytes).decode(),
        "branch": GITHUB_BRANCH,
    })


def add_stories(slides: list[dict], deck_name: str = "story") -> int:
    """slides: [{"heading": str, "note": str, "images": [(filename, bytes), ...]}, ...]
    Returns how many stories were written."""
    start = _next_index()
    for offset, slide in enumerate(slides):
        idx = start + offset
        slug = _slugify(slide.get("heading", ""), deck_name)
        folder = f"stories/{idx:02d}-{slug}"
        msg = f"Add story: {slide.get('heading') or folder}"
        _put_file(f"{folder}/heading.txt", (slide.get("heading", "") + "\n").encode(), msg)
        _put_file(f"{folder}/note.txt", (slide.get("note", "") + "\n").encode(), msg)
        for i, (filename, data) in enumerate(slide.get("images", []), start=1):
            ext = Path_suffix(filename) or ".jpg"
            _put_file(f"{folder}/photos/img_{i}{ext}", data, msg)
    return len(slides)


def delete_story(name: str) -> None:
    if not re.match(r"^[\w\-]+$", name):
        raise ValueError("bad story name")
    entries = [e for e in _tree() if e["path"].startswith(f"stories/{name}/") and e["type"] == "blob"]
    for e in entries:
        _request("DELETE", f"{API_BASE}/contents/{e['path']}", {
            "message": f"Remove story: {name}",
            "sha": e["sha"],
            "branch": GITHUB_BRANCH,
        })


def load_config() -> dict:
    data = _request("GET", f"{API_BASE}/contents/issue.json?ref={GITHUB_BRANCH}")
    if not data:
        return {"org_small": "ANKUR", "title_big": "NEWSLETTER", "date_range": "",
                "style": "ankur-classic", "output_file": "output/newsletter.pdf"}
    return json.loads(base64.b64decode(data["content"]))


def save_config(config: dict) -> None:
    existing = _request("GET", f"{API_BASE}/contents/issue.json?ref={GITHUB_BRANCH}")
    sha = existing["sha"] if existing else None
    body = {
        "message": "Update masthead config",
        "content": base64.b64encode((json.dumps(config, indent=2) + "\n").encode()).decode(),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        body["sha"] = sha
    _request("PUT", f"{API_BASE}/contents/issue.json", body)
