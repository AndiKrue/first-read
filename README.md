# FIRST READ

FIRST READ turns one Fountain scene into a storyboard, a performed cast read, and a downloadable animatic.

## The problem

A screenwriter can read a script a hundred times and never hear it. Storyboards and table reads cost money and access, so scripts are often judged as text by people who cannot yet see or hear them. FIRST READ gives a writer a first visual and vocal pass from the page.

## What it does

1. Accepts one Fountain scene and parses its setting, action, dialogue, and cast.
2. Produces a validated four-to-six-beat breakdown after looking up existing characters.
3. Generates or recalls a persistent character sheet for each cast member.
4. Renders storyboard panels from the beat, staging, wardrobe, and character-sheet references.
5. Casts persistent voices and performs all dialogue in one multi-speaker read.
6. Uses ffmpeg to time the panels against the measured audio and mux a downloadable MP4 animatic.

## Try it

**Hosted demo:** no deployment URL is committed in this source tree, so no unverifiable URL is presented here.

**One-command local path:** copy `.env.example` to `.env`, fill in the Google Cloud and GCS values, create local Application Default Credentials with `gcloud auth application-default login`, then run:

```bash
docker compose up --build
```

Open `http://localhost:8080`. `docker-compose.yml` starts the application and a persistent local ClickHouse service.

**Full local setup:** use Python 3.12, Node 20+, ffmpeg, Application Default Credentials, and a ClickHouse HTTP endpoint. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
: "Fill in .env before continuing."
npm --prefix web ci
npm --prefix web run build
uvicorn first_read.api:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. The browser sends generation requests to `POST /api/previz`, polls `GET /api/runs/{run_id}`, and retrieves durable runs, samples, and production-memory search through `GET /api/runs`, `GET /api/samples`, and `GET /api/search`.

## Architecture

- `src/first_read/agent.py` defines an ADK `LlmAgent` root with breakdown, storyboard, table-read, and assembly sub-agents exposed as `AgentTool` instances. This graph is the Gemini Enterprise Agent Platform integration surface; `src/first_read/api.py` runs the same stage functions in order as a background task for the web application.
- `gemini-2.5-pro` handles ADK orchestration and breakdown reasoning, with `gemini-2.5-flash` as the fallback; `gemini-2.5-flash-image` creates character sheets and storyboard panels; `gemini-2.5-pro-tts` performs the multi-speaker audio.
- ClickHouse stores indexed asset metadata, reusable character continuity, and durable run state. Direct writes and read-only MCP queries are deliberately separate.
- Google Cloud Storage holds character sheets, panels, WAV table reads, and MP4 animatics under stable `gs://` identifiers; the API turns those identifiers into browser URLs.
- ffmpeg gives each panel a uniform hold of at least 2.5 seconds, pads short audio when needed, and encodes H.264 video with AAC audio.

## Why ClickHouse

ClickHouse is the production memory used during generation and replay, not an analytics add-on.

- The `assets` table indexes every sheet, panel, audio file, and animatic by run, asset type, script, scene, beat, shot, interior/exterior, time of day, location, characters, tone, prompt, model, GCS URI, and duration.
- The `characters` table keeps each name's visual description, scene wardrobe, voice casting, script title, and reusable character-sheet URI.
- The `runs` table checkpoints stage, error, breakdown, panel URIs, audio URI, animatic URI, duration, and scene metadata. `GET /api/runs` and `GET /api/runs/{run_id}` read the latest stored version, which makes completed and partial runs available after a process restart.
- DDL and inserts travel through `clickhouse-connect` in `src/first_read/store.py`. Each produced asset is inserted immediately, and run transitions append versions to a `ReplacingMergeTree`.
- Reads travel through the official `mcp-clickhouse` MCP server wired as an ADK `McpToolset` in `src/first_read/memory.py`. The stdio server is forced read-only and exposes only query and metadata tools.

The breakdown queries characters by name before generation. Stored visual description, wardrobe, voice, and sheet URI are returned to the pipeline; remembered descriptions and voices override new model proposals, character sheets are reused, and the same stored attributes therefore carry across scenes and scripts that use the same character name.

This is the exact character lookup issued for JUNE:

```sql
SELECT name, visual_description, wardrobe, voice_name, sheet_gcs_uri
FROM characters FINAL
WHERE name IN ('JUNE')
```

The repository contains no captured production row for that query. An invented result would not be evidence; after running either sample, a reviewer can obtain the actual deployment-specific output through the `clickhouse_run_query` MCP tool or the same read path exercised by `GET /api/search?character=JUNE`.

## Evidence ledger

| Claim | File and function | How to verify |
|---|---|---|
| The browser provides sample scenes, live progress, panels, synchronized audio, MP4 download, search, and run replay in one workflow. | `web/src/App.tsx` — `App`, `hearIt`, `poll`, `syncPanel`, `search` | Start the local stack, submit a sample, and inspect each rendered state through completion. |
| No hosted deployment URL is committed. | `deploy.sh` — deployment entry point | Run `rg 'run\.app|https://.*first-read' README.md deploy.sh deploy.ps1`; no hosted service URL is returned. |
| A request accepts exactly one Fountain scene and extracts setting, action, dialogue, and cast. | `src/first_read/parse.py` — `parse_fountain` | Run `PYTHONPATH=src python -c "from first_read.parse import parse_fountain; print(parse_fountain('samples/sample_scene.fountain'))"`; add a second slugline and observe the explicit rejection. |
| Breakdown consults character memory, validates 4–6 beats, and falls back between the two reasoning models. | `src/first_read/tools/breakdown.py` — `generate_breakdown`; `src/first_read/schema.py` — `Breakdown.validate_beat_count` | Inspect the `lookup_characters` call, the two-model loop, and the beat-count validator. |
| Character sheets are generated once, stored, and supplied as panel references. | `src/first_read/tools/storyboard.py` — `ensure_character_sheet`, `generate_storyboard` | Inspect the remembered `sheet_gcs_uri` branch and the `reference_uris` passed to `_generate_image`. |
| The read uses persistent voice casting and one multi-speaker request. | `src/first_read/tools/tableread.py` — `_cast`, `generate_table_read` | Inspect the sorted fixed roster and the single `generate_content` call containing every speaker configuration. |
| ffmpeg times and muxes the final H.264/AAC MP4. | `src/first_read/tools/assemble.py` — `_panel_timing`, `_ffmpeg_command`, `assemble_animatic` | Inspect the generated command, minimum hold calculation, optional audio padding, and checked return code. |
| The browser API exposes generation, polling, replay, samples, and search. | `src/first_read/api.py` — `create_previz`, `get_run`, `get_runs`, `get_samples`, `search_production_memory` | Open `/docs` locally or run `PYTHONPATH=src python -c "from first_read.api import app; print(sorted(r.path for r in app.routes))"`. |
| The ADK graph contains 4 sub-agents and a read-only MCP toolset. | `src/first_read/agent.py` — `root_agent`; `src/first_read/memory.py` — `get_mcp_toolset` | Inspect `root_agent.tools`; confirm 4 `AgentTool` entries plus one `McpToolset`, and inspect the forced write/drop flags. |
| Every runtime model has the stated job. | `src/first_read/models.py` — model constants; generation calls in `src/first_read/tools/breakdown.py`, `src/first_read/tools/storyboard.py`, and `src/first_read/tools/tableread.py` | Run `rg 'REASONING_MODEL|REASONING_FALLBACK_MODEL|STORYBOARD_MODEL|TABLE_READ_MODEL' src/first_read`. |
| ClickHouse stores assets, characters, and durable run versions through direct writes. | `src/first_read/store.py` — `initialize_schema`, `insert_asset`, `upsert_character`, `upsert_character_sheet`, `upsert_run` | Inspect the three DDL constants and run `python smoke/02_clickhouse_mcp.py` against configured ClickHouse credentials. |
| ClickHouse reads use the official MCP server for character lookup, asset search, and replay. | `src/first_read/memory.py` — `get_mcp_toolset`, `_run_query`, `lookup_characters`, `search_assets`, `list_runs`, `get_run` | Inspect the stdio command and call chain; run `python smoke/02_clickhouse_mcp.py`, then `python smoke/04_pipeline.py http://localhost:8080`. |
| GCS stores generated media and supplies browser URLs. | `src/first_read/gcs.py` — `upload_bytes`, `signed_url_for_uri` | Inspect object upload and URI conversion, then check returned `panel_urls`, `audio_url`, and `animatic_url` from `GET /api/runs/{run_id}`. |
| The Docker Compose path starts the application with persistent local ClickHouse. | `docker-compose.yml` — `app`, `clickhouse`, `clickhouse-data` services/volume | After configuring `.env` and ADC, run `docker compose config`, then `docker compose up --build` and request `http://localhost:8080/healthz`. |
| The full local path uses the documented Python and Node versions and includes ffmpeg in the packaged runtime. | `pyproject.toml` — `requires-python`; `Dockerfile` — build stages and ffmpeg package | Inspect the version constraints and image stages, then build the container. |
| The deployment uses a Cloud Run runtime service account rather than local ADC. | `deploy.sh` — `gcloud run deploy`; `src/first_read/tools/breakdown.py` — `_client` | Inspect the deployment command and Vertex client construction; deploy, then verify the service identity's IAM roles before requesting `/healthz`. |
| Input, access, rate, and artwork limitations match the implementation. | `src/first_read/parse.py` — `parse_fountain`; `src/first_read/api.py` — `_claim_shared_run`, `create_previz`; `src/first_read/models.py` — `PANEL_STYLE` | Inspect the second-slugline rejection, absence of an authentication dependency, process-local rate deque, and storyboard style. |
| The project is licensed under AGPL-3.0. | `LICENSE` — licence text | Read the first line of `LICENSE`. |

## Configuration

Copy `.env.example` to `.env` and set:

| Variable | Requirement |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | Required Google Cloud project for Vertex AI and GCS. |
| `GOOGLE_CLOUD_LOCATION` | Required Vertex AI region; the example uses `us-central1`. |
| `GOOGLE_GENAI_USE_VERTEXAI` | Required and must equal `TRUE`. |
| `GCS_BUCKET` | Required bucket for generated media. |
| `CLICKHOUSE_HOST` | Required ClickHouse HTTP host. |
| `CLICKHOUSE_PORT` | Required HTTP port; the example uses `8443`. |
| `CLICKHOUSE_USER` | Required database user. |
| `CLICKHOUSE_PASSWORD` | Required database password. |
| `CLICKHOUSE_DATABASE` | Required database; the example uses `first_read`. |
| `CLICKHOUSE_SECURE` | Optional TLS switch; defaults to `true`. |

`FIRST_READ_OUTPUT_DIR` optionally changes the local working directory from `/tmp/first-read`. `MAX_RUNS_PER_HOUR` optionally changes the process-local unauthenticated generation cap from `3`.

Cloud Run uses its compute service account instead of a developer's Application Default Credentials. That service account needs `roles/aiplatform.user` for model calls and `roles/storage.objectAdmin` for generated media:

```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SERVICE_ACCOUNT" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SERVICE_ACCOUNT" --role="roles/storage.objectAdmin"
```

## Limitations

Each run accepts one scene in Fountain text only. The application has no authentication; the shared, no-key path has a process-local hourly rate cap, while an optional request-scoped Google API key bypasses that cap. Generated panels are storyboard-grade continuity references, not finished artwork.

## Licence and entry

Licensed under AGPL-3.0. This is an individual entry.
