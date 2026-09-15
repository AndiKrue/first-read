# WO-CINEMA-63 closeout

The public interface now carries forward the visual and structural work from these private work orders without importing their product features:

- WO-CINEMA-18 and WO-CINEMA-31: higher-contrast text and named surface, border, and decoration tokens.
- WO-CINEMA-37: bounded buttons, underlined links, 44px icon/control hit areas, visible interaction states, and a dark styled native audio player.
- WO-CINEMA-38 §3: a failed run leads with one human sentence and keeps the raw technical detail inside a disclosure.
- WO-CINEMA-41, WO-CINEMA-42, and WO-CINEMA-43: idea-first composition, adjacent rail/compose/workspace regions at 1024px and wider, compose-first stacking below 1024px, independent bounded scrollers, a measured 60-character compose width, and fixed action/tool regions.

The public product still contains only its existing scene writing, scene generation, storyboard, table-read, MP4 download, production-memory search, and run-replay capabilities. Annotations, likeness handling, voice matching, subtitles, checkpoints, audit code, and the private compliance module were deliberately not ported. The Impressum, privacy, and contact routes are static legal-information pages. There is no contact endpoint, data-subject endpoint, or legal-operator API in this repository. The compose surface does include the required plain-language AI disclosure.

## Shared ClickHouse compatibility

The public deployment shares ClickHouse with an actively developed private tree. This is the second defect caused by that arrangement: first, an interrupted private run left an empty `panel_uris` entry that made the public run gallery fail; second, WO-CINEMA-62 replaces the `characters` table while the public service still depended on it.

Public character lookups now read the current row for every identity from `characters_v3` by grouping on `character_id` and applying `argMax(..., version)` to each value:

```sql
SELECT name, visual_description, wardrobe, voice_name, sheet_gcs_uri
FROM (
  SELECT
    character_id,
    argMax(name, version) AS name,
    argMax(visual_description, version) AS visual_description,
    argMax(wardrobe, version) AS wardrobe,
    argMax(voice_name, version) AS voice_name,
    argMax(sheet_gcs_uri, version) AS sheet_gcs_uri,
    argMax(created_at, version) AS created_at
  FROM characters_v3
  GROUP BY character_id
)
WHERE name IN (...)
ORDER BY created_at, character_id
```

The public write boundary appends schema-compatible `characters_v3` rows so a public process cannot recreate the retired table at startup. It does not expose identity, cloning, version selection, or history. Run serialization now ignores empty or malformed media URIs; one damaged panel therefore no longer makes `GET /api/runs` fail.

The `characters_v3` query and schema must still be checked against the real shared database after WO-CINEMA-62 is applied. That production verification is host/deployment work and was not represented as a builder result here.
