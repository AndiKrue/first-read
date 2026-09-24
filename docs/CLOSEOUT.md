# Closeout

## WO-PUBLIC-01 — a failed run can be resumed in place

### The endpoint

`POST /api/runs/{run_id}/resume`, body `{"fountain_text": string | null}` (an
empty body counts as `null`). It answers `202 {"run_id": ..., "resume":
"partial" | "early"}` and finishes the run in the background, the same way
`POST /api/previz` does. Progress is visible through `GET /api/runs/{run_id}` and
the gallery. The run keeps its `run_id`, title and `created_at`, so the same
gallery card completes.

| Answer | When |
|---|---|
| 404 `Not Found` | The admin token is missing or wrong, or `FIRST_READ_ADMIN_TOKEN` is unset. This is checked before anything else, including the body. |
| 404 `Run not found` | The token is right, but no run has this id. |
| 409 | The run is not `failed` or `cancelled` (a `done` run included), this process is already resuming it, or its stored breakdown cannot be read. |
| 422 | The scene is needed but was not sent, the scene cannot be parsed, a partial run was sent a different scene, or the body is not JSON. |

The stored `error` stays while the resume runs. It is cleared when the run reaches
`done` and replaced if the run fails again. A run that fails again can be resumed
again.

### The token

The service had no administrative token before this work order, so the route uses
a new one: set `FIRST_READ_ADMIN_TOKEN` in the service environment and send it
as `Authorization: Bearer <token>`. The comparison is constant-time. While the
variable is unset or blank, the route answers 404 to everyone. The route is left
out of the OpenAPI schema (`/docs`, `/openapi.json`), and no UI calls it.
`deploy.sh` forwards every variable in `.env`, so adding
`FIRST_READ_ADMIN_TOKEN=...` there is enough. Like the other credentials it
passes, the token then becomes a plain Cloud Run environment variable.

### Partial and early runs

- **Partial** means `breakdown_json` is stored. The run resumes from that
  breakdown, and the breakdown is not generated again. Stored panels are placed
  at the beat each one depicts, using this order of rules:
  1. The storyboard's own object name, `runs/<run_id>/panel_NN.png`.
  2. `panel_beat_ids`, when it lines up with `panel_uris`.
  3. Otherwise, stored order.

  Empty or malformed entries (such as the empty fourth entry in
  `6152afee-460f-4ad4-814a-d22a29394262`) are treated as missing panels. Only the
  missing beats are drawn, each under its own index (`generate_storyboard(...,
  positions=...)`), so a kept panel's object is never written over. Kept panels
  are downloaded for assembly, not redrawn. Then the table read runs, then the
  animatic is assembled. Every version written during the resume carries
  `panel_uris` in beat order, with `panel_beat_ids` restored from the
  breakdown's beats, in order. `38e808ef-7390-4aaa-8ad8-a4083bf38122`, which has
  no beat ids, gets them in its first written version. Before drawing or
  reading, characters missing from `characters` (for example, recorded only in
  the old `characters_v3`) are recorded, and remembered voices are used.
- **Early** means there is no breakdown. `fountain_text` is required; without it
  the answer is 422 with a sentence saying the scene is needed. The run then
  goes through the new-run pipeline unchanged (`queued` → `parsing` →
  `breakdown` → panels → `reading` → `assembling` → `done`) under its existing
  `run_id` and title.

**Assumption beyond the work order:** the table read performs the scene's
dialogue, and no table stores scene text; the breakdown holds only what the
camera sees. A partial run whose read has not been made, which covers both
measured partial runs, therefore also needs `fountain_text`. Without it the
answer is 422 with a sentence saying the scene is needed for its dialogue. The
scene is checked against the stored run: the slugline and the breakdown's
characters must match. A partial run that already has its read (it failed while
assembling) resumes without the scene.

### Schema and `POST /api/previz`

The DDL is unchanged, and nothing adds a column. `panel_beat_ids` exists in the
shared production `runs` table (the private tree writes it), but not in this
service's DDL. The resume read (`memory.get_run_for_resume`, over MCP) selects
it only when `system.columns` lists it. `store.upsert_run` writes it only when a
record has beat ids *and* the table has the column. A new run never has beat ids,
so `POST /api/previz` inserts exactly the columns it always did. Its request,
answer, hourly cap and pipeline are unchanged, and no run is deleted.

`generate_storyboard` without `positions` renders every beat as before. The
only change to the new-run pipeline is `record.error = ""` just before `done`,
and for a new run the error is already empty at that point.

### Resuming one run

```bash
# A partial or early run: resend its scene.
curl -X POST "$FIRST_READ_URL/api/runs/6152afee-460f-4ad4-814a-d22a29394262/resume" \
  -H "Authorization: Bearer $FIRST_READ_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  --data "$(jq -Rs '{fountain_text: .}' samples/sample_scene.fountain)"

# A partial run that already has its read: no scene needed.
curl -X POST "$FIRST_READ_URL/api/runs/<run_id>/resume" \
  -H "Authorization: Bearer $FIRST_READ_ADMIN_TOKEN" \
  -H "Content-Type: application/json" --data '{"fountain_text": null}'
```

Then poll `GET /api/runs/<run_id>` until `stage` is `done`.

### Verification

`python -m pytest tests` (48 tests; the repository had none before), `ruff check
src` and `ruff format --check src` all pass. Before this change, `ruff check src`
reported one error (import order in `tools/storyboard.py`), and `ruff format
--check src` flagged `tools/storyboard.py` and `schema.py`; both files are now
clean.

The tests cover:

- A partial run keeps its stored panel URIs, and only the missing panel is
  uploaded.
- A run without beat ids gets them from its breakdown, in beat order.
- An early run reaches `done` under its original `run_id`, `created_at` and
  title.
- An early run without the scene gets 422.
- `done` and in-progress runs get 409.
- Every missing-token variant gets 404, a malformed body included.
- `POST /api/previz` still completes, writes no beat ids, and keeps its 429 cap.

Not verified here: no request reached the real shared ClickHouse or GCS. The
column check and the ten real runs remain host/deployment work. The duplicate
resume guard is per process; the service runs up to three instances.
