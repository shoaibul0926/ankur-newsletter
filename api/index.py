"""
Vercel Python entry point — exposes the existing Flask app as a WSGI
callable at the project root, per Vercel's @vercel/python convention.

Heads up (see vercel.json comment / deployment notes): Vercel's filesystem
is read-only outside /tmp, and /tmp isn't shared across invocations. This
app's "Add story", "Upload deck", "Delete story", and "Save masthead"
routes write to files under the project directory (stories/, issue.json),
which works for local use but will fail or silently not persist here.
Viewing the queued stories and generating a PDF from what's already in
the repo should still work.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402
