"""
AI-assisted article condensing.

Turns raw, verbose slide/page text into a short newsletter-style article
(1-2 sentences, at most one ++highlighted++ fact) the way a human editor
trims it -- the same thing done by hand for the July-Sept stories, now
automatic. Called by extract.py right after pulling raw text off a
slide/page, so a story never ships as a raw bullet dump.

Requires ANTHROPIC_API_KEY (copy .env.example to .env and fill it in).
If it's not set, or the call fails for any reason, this is NOT a hard
dependency -- the raw extracted text is kept as-is and a note is printed,
so extraction still works end to end without a key.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

_client = None
_warned_no_key = False


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


SYSTEM_PROMPT = """You edit raw event notes into a short newsletter article, in the exact \
style of a real school newsletter.

Rules:
- Use ONLY facts present in the raw text. Never invent names, dates, numbers, or details.
- Write 1-2 short sentences of plain prose, not bullet points.
- Mark AT MOST one key name/fact with ++like this++ -- the single most newsworthy detail \
(an award, a name, a date). Never use ++ more than once.
- Do not use any other markup (no **, no lists, no headings).
- Warm, proud, concise tone, e.g.: "Ankur celebrated its ++Foundation Day++ on 9th July \
with a special lunch, joined by the full staff team."
- If the raw text covers several unrelated events, cover all of them briefly in one or two \
sentences total rather than dropping any -- compress, don't omit.
- Output ONLY the article text. No preamble, no quotes, no explanation, no markdown fences.
"""


def condense_article(heading: str, raw_text: str) -> str:
    """Condense raw_text into a short article.

    Returns raw_text unchanged (with a console note) if no API key is
    configured or the call fails -- this is a quality upgrade, never a
    hard requirement for extraction to work.
    """
    global _warned_no_key
    raw_text = raw_text.strip()
    if not raw_text:
        return raw_text

    client = _get_client()
    if client is None:
        if not _warned_no_key:
            print("NOTE: ANTHROPIC_API_KEY not set -- keeping raw extracted text as-is. "
                  "Copy .env.example to .env and add a key to enable AI condensing.")
            _warned_no_key = True
        return raw_text

    try:
        response = client.messages.create(
            model="claude-opus-5-5",
            max_tokens=400,
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Heading: {heading or '(none)'}\n\nRaw notes:\n{raw_text}",
            }],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or raw_text
    except Exception as e:
        print(f"WARNING: AI condensing failed ({e}) -- keeping raw extracted text.")
        return raw_text
