"""Builds the review screen from its one source file.

    uv run python -m preparer.build            writes both outputs
    uv run python -m preparer.build --check    exits 1 when either output is stale

From preparer/app/review.html it writes preparer/app/dist/review.html, which the server
serves, and docs/review-demo.html, the static demo for the teardown site. The demo embeds
the three demo drafts in preparer/app/demo_drafts, each as the view would get it from
open_review, so the page runs a fake tool layer in memory, says "Demo: nothing is saved,"
and needs no network. Both outputs start with a comment naming the source's SHA-256, and
tests/test_preparer_build.py fails when either no longer matches a fresh build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Sequence

from preparer.drafts import DraftFolder, Reference, compute_flags
from preparer.server import review_payload

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "preparer" / "app"
SOURCE = APP_DIR / "review.html"
DIST = APP_DIR / "dist" / "review.html"
DEMO_PAGE = REPO / "docs" / "review-demo.html"
DEMO_DRAFTS = APP_DIR / "demo_drafts"
DOCTYPE = "<!doctype html>\n"
SOURCE_TITLE = "<title>Preparer review</title>"
DEMO_TITLE = "<title>The preparer's review screen: demo</title>"
DEMO_DESCRIPTION = (
    '<meta name="description" content="A working demo of the preparer\'s review screen from What Should My Tax Agent Ask?, '
    'with three synthetic drafts and nothing saved.">'
)
MODULE_SCRIPT = '<script type="module">'


def header(source: str) -> str:
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"<!-- Built by preparer/build.py from preparer/app/review.html, source sha256 {sha}. Edit the source, then rebuild. -->\n"


def demo_data(demo_dir: Path = DEMO_DRAFTS, ref: Reference | None = None) -> str:
    """The demo drafts as one JSON array, ASCII only and safe inside a script element."""
    ref = ref or Reference.load()
    folder = DraftFolder(demo_dir, ref)
    payloads = []
    for draft_id, draft in folder.load_all().items():
        payloads.append(review_payload(draft, compute_flags(draft, ref), ref, None))
    text = json.dumps(payloads, ensure_ascii=True, separators=(",", ":"))
    return text.replace("</", "<\\/")


def build(source: str, demo_json: str) -> tuple[str, str]:
    """The served view and the demo page, from the source and the embedded demo data."""
    if not source.startswith(DOCTYPE) or SOURCE_TITLE not in source or source.count(MODULE_SCRIPT) != 1:
        raise ValueError("preparer/app/review.html must start with <!doctype html>, keep its title, and have one module script")
    body = source[len(DOCTYPE):]
    dist = DOCTYPE + header(source) + body
    demo_body = body.replace(SOURCE_TITLE, DEMO_TITLE + "\n" + DEMO_DESCRIPTION, 1)
    demo_body = demo_body.replace(
        MODULE_SCRIPT, f'<script id="demo-data" type="application/json">{demo_json}</script>\n{MODULE_SCRIPT}', 1
    )
    return dist, DOCTYPE + header(source) + demo_body


def outputs() -> dict[Path, str]:
    dist, demo = build(SOURCE.read_text(encoding="utf-8"), demo_data())
    return {DIST: dist, DEMO_PAGE: demo}


def stale() -> list[Path]:
    return [path for path, text in outputs().items() if not path.exists() or path.read_text(encoding="utf-8") != text]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 when an output is stale, and write nothing")
    args = parser.parse_args(argv)
    if args.check:
        old = stale()
        for path in old:
            print(f"stale: {path.relative_to(REPO)}")
        return 1 if old else 0
    for path, text in outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
