"""SMOKE 04 - run both sample scenes end to end and pull the output down for inspection.

Usage:
    python smoke/04_pipeline.py                 # local container on 8099
    python smoke/04_pipeline.py <base_url>      # e.g. the Cloud Run URL

Writes everything to out/smoke/<host>/ so a local run and a deployed run can be
compared side by side. Panels are named beat-ordered, so a directory listing is
the storyboard in order.

What to look at afterwards:
  - panels have DIFFERENT framing (the shot types should be visibly distinct)
  - no rain or weather INSIDE the interior scene
  - characters stay on the same side of the table across panels
  - the exterior scene reuses the same two faces from the interior scene
"""

import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8099").rstrip("/")
HOST = urllib.parse.urlparse(BASE).netloc.replace(":", "_")
import datetime
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = pathlib.Path("out/smoke") / HOST / STAMP
OUT.mkdir(parents=True, exist_ok=True)

SCENES = [
    ("01-interior", "The Last Departure", "samples/sample_scene.fountain"),
    ("02-exterior", "The Last Departure", "samples/second_scene.fountain"),
]


def post_json(path, payload):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as response:
        return json.loads(response.read())


def download(url, destination):
    with urllib.request.urlopen(url, timeout=120) as response:
        destination.write_bytes(response.read())


def run_scene(label, title, fountain_path):
    text = pathlib.Path(fountain_path).read_text(encoding="utf-8")
    print(f"\n=== {label}  ({fountain_path}) ===")

    started = time.time()
    accepted = post_json("/api/previz", {"script_title": title, "fountain_text": text})
    run_id = accepted["run_id"]
    print(f"run_id {run_id}")

    last_stage = None
    state = {}
    while time.time() - started < 900:
        state = get_json(f"/api/runs/{run_id}")
        stage = state.get("stage")
        if stage != last_stage:
            print(f"  [{time.time() - started:6.1f}s] {stage}")
            last_stage = stage
        if stage in ("done", "failed", "cancelled"):
            break
        time.sleep(2)

    elapsed = time.time() - started
    print(f"  finished as {state.get('stage')} in {elapsed:.1f}s")

    if state.get("error"):
        print(f"  ERROR: {state['error']}")

    breakdown = state.get("breakdown") or {}
    if breakdown:
        (OUT / f"{label}-breakdown.json").write_text(
            json.dumps(breakdown, indent=2), encoding="utf-8"
        )
        print(f"  tone: {breakdown.get('tone')}")
        if breakdown.get("staging"):
            print(f"  staging: {breakdown['staging']}")
        for beat in breakdown.get("beats", []):
            present = ",".join(beat.get("characters_present", []))
            print(f"    beat {beat.get('beat_id')}  {beat.get('shot_type'):<6}  [{present}]")

    for index, url in enumerate(state.get("panel_urls") or [], start=1):
        target = OUT / f"{label}-panel-{index:02d}.png"
        try:
            download(url, target)
            print(f"  saved {target.name}")
        except Exception as error:  # noqa: BLE001 - report and continue
            print(f"  FAILED {target.name}: {error}")

    if state.get("audio_url"):
        try:
            download(state["audio_url"], OUT / f"{label}-read.wav")
            print(f"  saved {label}-read.wav ({state.get('audio_duration_seconds')}s)")
        except Exception as error:  # noqa: BLE001
            print(f"  FAILED audio: {error}")

    if state.get("animatic_url"):
        try:
            download(state["animatic_url"], OUT / f"{label}-animatic.mp4")
            print(f"  saved {label}-animatic.mp4")
        except Exception as error:  # noqa: BLE001
            print(f"  FAILED animatic: {error}")

    return state


def main():
    print(f"target: {BASE}")
    print(f"output: {OUT.resolve()}")

    results = [run_scene(*scene) for scene in SCENES]

    print("\n=== character reuse ===")
    print("The exterior scene should NOT have regenerated JUNE and MARCO.")
    for label, state in zip((s[0] for s in SCENES), results):
        characters = ((state.get("breakdown") or {}).get("characters")) or []
        for character in characters:
            description = (character.get("visual_description") or "")[:60]
            print(f"  {label}  {character.get('name'):<8} {character.get('voice_name'):<10} {description}")
    print("\nIf the two descriptions for a character differ, memory reuse is NOT working.")

    print("\n=== search ===")
    for query in ("character=JUNE", "int_ext=EXT", "tone=tense"):
        try:
            hits = get_json(f"/api/search?{query}")
            print(f"  {query:<18} -> {len(hits['assets'])} assets")
        except Exception as error:  # noqa: BLE001
            print(f"  {query:<18} -> FAILED: {error}")

    print(f"\nOpen {OUT.resolve()} and look at the panels.")


if __name__ == "__main__":
    main()
