# Import Worker — Integration Specification

Any external system (n8n, Make, scripts, other instances) can deliver clips
for automatic YouTube publication by simply copying files into the `imports/` folder.

---

## Folder structure

```
yt-pub-lives<N>/
  imports/
    batch-name/          ← one folder = one publication batch
      clip_01_Title.mp4
      clip_02_Other.mp4
      manifest.json        ← optional, but recommended
```

- The folder name becomes the **batch title** in the dashboard (can be overridden in the manifest).
- Each folder is processed independently.
- After processing, the folder is **automatically removed** from `imports/`.

---

## manifest.json — Full format

```json
{
  "titulo":     "Batch name in dashboard",
  "publish_at": "14:00",
  "privacy":    "public",
  "clips": [
    {
      "filename":    "clip_01_Title.mp4",
      "title":       "Video title on YouTube",
      "description": "Full video description.",
      "tags":        ["tag1", "tag2", "tag3"]
    },
    {
      "filename":    "clip_02_Other.mp4",
      "title":       "Second video",
      "description": "",
      "tags":        []
    }
  ]
}
```

### Root manifest fields

| Field        | Type   | Required | Description |
|--------------|--------|----------|-------------|
| `titulo`     | string | no       | Batch name in dashboard. Default: folder name |
| `publish_at` | string | no       | Publish time HH:MM (e.g. `"14:00"`). Only respected if `import_fila_global=false` in config |
| `privacy`    | string | no       | `public` \| `unlisted` \| `private`. Overrides the global config for this batch |
| `clips`      | array  | no       | Per-file metadata list. If absent, uses filenames |

### Per-clip fields

| Field         | Type         | Required | Description |
|---------------|--------------|----------|-------------|
| `filename`    | string       | yes      | Exact name of the MP4 file in the same folder |
| `title`       | string       | no       | Title on YouTube. Default: filename without extension |
| `description` | string       | no       | Description. If empty and `import_gerar_descricao=true`, AI generates it automatically |
| `tags`        | string array | no       | Video tags |

---

## Without manifest — default behavior

If there is no `manifest.json`, the system:

1. Uses all `.mp4` files in the folder in alphabetical order
2. Title = filename without extension and without numeric prefix
   - `clip_01_How to use n8n.mp4` → `"How to use n8n"`
   - `03_Basic tutorial.mp4` → `"Basic tutorial"`
3. Description = empty (or AI-generated if `import_gerar_descricao=true`)
4. Tags = empty
5. Privacy = global config value

---

## System config (configuration panel)

| Key                      | Values          | Default   | Description |
|--------------------------|-----------------|-----------|-------------|
| `import_auto`            | `true`\|`false` | `false`   | Automatic hourly scan of the imports/ folder |
| `import_gerar_descricao` | `true`\|`false` | `false`   | Generate description via AI when absent in manifest |
| `import_fila_global`     | `true`\|`false` | `true`    | `true` = enters the normal queue (`pub_horarios`); `false` = respects `publish_at` from manifest |

---

## When clips are published

### Global queue mode (`import_fila_global=true`)

Imported clips enter the **same queue** as clips cut from live streams.
They are published according to the `pub_horarios` schedule in config.

```
imports/batch/ → processed → global queue → pub_horarios → YouTube
```

### Own schedule mode (`import_fila_global=false`)

The system respects the `publish_at` defined in `manifest.json`.
If the current time is earlier than `publish_at`, the batch waits.

```
manifest.json: { "publish_at": "14:00" }
→ clips not published before 14:00
→ from 14:00 onwards: enters the next publication round
```

If `publish_at` is not set in the manifest, the batch also enters the global queue.

---

## Manual trigger via API

```http
POST /api/import/scan
Content-Type: application/json
{}
```

Response:
```json
{
  "ok": true,
  "processados": 2,
  "total": 2,
  "detalhes": [
    { "pasta": "lote-01", "video_id": "import_20260404_lote_01", "clips": 5, "ok": true },
    { "pasta": "lote-02", "video_id": "import_20260404_lote_02", "clips": 3, "ok": true }
  ]
}
```

---

## Cleanup via API

```http
POST /api/import/clean
Content-Type: application/json
{ "action": "imports" }
```

| `action`    | What it does |
|-------------|--------------|
| `imports`   | Removes leftover folders in `imports/` (unprocessed) |
| `clips`     | Removes the `clips/` folder from **fully published** live streams |
| `clips_all` | Removes the `clips/` folder from **all** live streams (use with caution) |

---

## n8n/Make integration example

1. Generate clip MP4s via an external cutting pipeline
2. Create `manifest.json` with titles, descriptions, and `publish_at`
3. Copy everything to `imports/batch-name/` via SSH/SCP or shared volume
4. Call `POST /api/import/scan` to process immediately
   (or wait for the automatic hourly scan if `import_auto=true`)

---

## Direct CLI

```bash
# Process imports/ manually
python3 import_worker.py scan

# Clean imports/ (leftovers)
python3 import_worker.py clean-imports

# Clean clips/ from fully published live streams
python3 import_worker.py clean-clips

# Clean clips/ from all live streams
python3 import_worker.py clean-clips --all
```
