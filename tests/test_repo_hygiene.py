"""Repo-wide rules from CLAUDE.md that code can check."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "mutants", "runs", "state", "html"}
# Never read .env: it holds the key once Roanuk adds it.
SKIP_FILES = {".env", "uv.lock"}
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".jsonl", ".csv", ".toml", ".txt", ".html", ".css", ".js", ".example", ""}

DASHES = re.compile("[" + chr(0x2013) + chr(0x2014) + "]")
BRITISH = re.compile(
    r"\b(colour|behaviour|favour|honour|labour|organisation|organise[ds]?|analyse[ds]?|licence|catalogue|centre|"
    r"modelling|labelled|travelling|cancelled|cancelling|programme|defence|whilst|amongst|artefact|"
    r"(?:real|recogn|summar|priorit|normal|optim|minim|maxim|util|initial|serial|final|categor|standard|emphas|author)is(?:e|es|ed|ing|ation))\b",
    re.IGNORECASE,
)
KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")
KEY_ASSIGNMENT = re.compile(r"^\s*(export\s+)?ANTHROPIC_API_KEY\s*=", re.MULTILINE)
ENVIRON_WRITE = re.compile(
    r"(environ\[\s*['\"]ANTHROPIC_API_KEY['\"]\s*\]\s*=|environ\.setdefault\(\s*['\"]ANTHROPIC_API_KEY|putenv\(\s*['\"]ANTHROPIC_API_KEY)"
)


def repo_text_files():
    for path in REPO.rglob("*"):
        rel = path.relative_to(REPO)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or not path.is_file():
            continue
        if path.name in SKIP_FILES or path.suffix not in TEXT_SUFFIXES:
            continue
        yield path


FILES = sorted(repo_text_files())


def test_scan_sees_files():
    names = {p.name for p in FILES}
    assert {"CLAUDE.md", "PRD-tax-agent-ask.md", "pyproject.toml"} <= names


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_em_or_en_dashes(path):
    hits = [i + 1 for i, line in enumerate(path.read_text(errors="replace").splitlines()) if DASHES.search(line)]
    assert not hits, f"em or en dash on lines {hits}"


# This file has to spell the words it forbids.
SPELLING_FILES = [p for p in FILES if p.name != "test_repo_hygiene.py"]


@pytest.mark.parametrize("path", SPELLING_FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_american_spelling(path):
    found = sorted({m.group(0) for m in BRITISH.finditer(path.read_text(errors="replace"))})
    assert not found, f"British spellings: {found}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_api_keys_or_key_assignments(path):
    text = path.read_text(errors="replace")
    assert not KEY_PATTERN.search(text), "something that looks like an Anthropic key"
    assert not KEY_ASSIGNMENT.search(text), "ANTHROPIC_API_KEY assigned in a file"
    if path.suffix == ".py":
        assert not ENVIRON_WRITE.search(text), "ANTHROPIC_API_KEY written into os.environ"


def test_dotenv_is_ignored():
    ignored = (REPO / ".gitignore").read_text().splitlines()
    assert ".env" in ignored
    assert "eval/runs/" in ignored
    assert "server/state/" in ignored
