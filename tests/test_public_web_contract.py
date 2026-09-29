"""Static guard for the public demo's browser API surface."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_browser_fetch_has_a_public_route():
    api = (ROOT / "src/first_read/api.py").read_text(encoding="utf-8")
    routes = {
        path
        for path in re.findall(r'@app\.(?:get|post|put|patch|delete)\("([^"]+)', api)
        if path.startswith("/api/") and not path.endswith(("/resume", "/score"))
    }
    calls = []
    for source in (ROOT / "web/src").rglob("*.tsx"):
        content = source.read_text(encoding="utf-8")
        for match in re.finditer(r"fetch\(\s*([\x27\x22`])(.+?)\1", content):
            path = match.group(2).split("?", 1)[0]
            path = re.sub(r"\$\{[^}]+\}", "{run_id}", path)
            calls.append((str(source.relative_to(ROOT)), path))
    assert calls
    assert [(source, path) for source, path in calls if path not in routes] == []


def test_restricted_private_surfaces_are_absent_from_browser_code():
    terms = re.compile(r"\b(?:billing|revenuecat|ads|consent)\b", re.IGNORECASE)
    offenders = []
    for source in (ROOT / "web/src").rglob("*"):
        if source.is_file() and terms.search(source.read_text(encoding="utf-8")):
            offenders.append(str(source.relative_to(ROOT)))
    assert offenders == []
