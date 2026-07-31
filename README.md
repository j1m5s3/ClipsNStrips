# ClipsNStrips

Python CLI for discovering popular YouTube videos, proposing highlight spans, creating
short clips and illustrated audio videos, and publishing reviewed outputs.

## Safety and rights model

Discovery does not grant permission to download or reuse a video. YouTube's terms generally
restrict downloading except when the service or rights holder authorizes it. Copyright and
fair use are fact-specific legal questions that this tool cannot decide.

ClipsNStrips therefore uses separate approval gates:

1. `ingest`: documents why the source may be acquired and processed.
2. `spans`: confirms the selected excerpts in their original context.
3. `rights`: confirms the reviewer has evaluated the final reuse.
4. `output`: confirms the rendered artifact is suitable to upload.
5. `publish`: separately authorizes changing a private upload to public.

Third-party sources, unclear licenses, licensed content, and music/movie categories are
flagged for manual review. Uploads are always private first. Approval records, source
metadata, prompts, provider IDs, checksums, and upload receipts remain in the job manifest.
This workflow is a risk control, not legal advice.

## Requirements

- Python 3.14 and [uv](https://docs.astral.sh/uv/)
- FFmpeg and FFprobe on `PATH`
- Google Cloud project with YouTube Data API v3 enabled
- YouTube API key for discovery
- OAuth desktop client JSON for uploads
- AssemblyAI API key
- Gemini API key
- OpenAI API key for the supported image-generation fallback

Midjourney is represented only by an adapter boundary because it has no generally available
official API. Discord automation and unofficial wrapper APIs are intentionally not used.

## Setup

```powershell
uv sync --dev
Copy-Item .env.example .env
uv run clipsnstrips doctor
```

Fill in `.env`. Place the Google OAuth desktop client file at `client_secret.json`, or
configure `YOUTUBE_OAUTH_CLIENT_FILE`. OAuth refresh tokens are written beneath `.secrets/`
and must not be committed.

YouTube API requests consume project quota. New or unverified API projects may be restricted
to private uploads until Google completes its required audit.

## Run scripts

PowerShell:

```powershell
.\scripts\setup.ps1
.\scripts\run.ps1 --help
.\scripts\run.ps1 discover --limit 5
.\scripts\test.ps1
```

Bash:

```bash
bash scripts/setup.sh
bash scripts/run.sh --help
bash scripts/run.sh discover --limit 5
bash scripts/test.sh
```

The launchers forward every argument to the ClipsNStrips CLI. They prefer `uv` and fall
back to the repository virtual environment when `uv` is not on `PATH`. On Unix-like systems,
you can optionally make the Bash scripts executable with `chmod +x scripts/*.sh`.

## Logging

Commands log to the console and to a rotating UTF-8 log file. The default location is
`OUTPUT_DIR/logs/clipsnstrips.log`. Override it in `.env`:

```dotenv
LOG_DIR=output/logs
LOG_LEVEL=INFO
LOG_FILENAME=clipsnstrips.log
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
```

Set `LOG_LEVEL=DEBUG` for FFmpeg command details and job persistence events. API keys,
OAuth tokens, full transcripts, and AI prompts are not written to logs.

## Workflow

Discover without writing job folders:

```powershell
uv run clipsnstrips discover --limit 10
```

`YOUTUBE_CATEGORY` and `--category` accept either a numeric API ID or a
case-insensitive name from
[`clipsnstrips/youtube/youtube_categories.json`](clipsnstrips/youtube/youtube_categories.json),
for example `Comedy` or `23`.

Create persistent jobs by adding `--no-dry-run`, or create one for a local source:

```powershell
uv run clipsnstrips discover --limit 10 --no-dry-run
uv run clipsnstrips create-local --title "Authorized source"
```

Record authorization and ingest. Notes should identify ownership, license, written
permission, or another reviewed authorization basis.

```powershell
uv run clipsnstrips approve JOB_ID ingest REVIEWER "Owned source; project archive"
uv run clipsnstrips ingest-local JOB_ID C:\media\source.mp4
```

An authorized YouTube source can use the guarded downloader:

```powershell
uv run clipsnstrips approve JOB_ID ingest REVIEWER "Written permission at CONTRACT_URL"
uv run clipsnstrips download-youtube JOB_ID "https://www.youtube.com/watch?v=..."
```

Analyze, inspect the returned candidates, select IDs, and approve the selection:

```powershell
uv run clipsnstrips analyze JOB_ID
uv run clipsnstrips select-spans JOB_ID SEGMENT_ID_1 SEGMENT_ID_2
uv run clipsnstrips approve JOB_ID spans REVIEWER "Checked timestamps in source context"
```

Render normal or 9:16 clips and illustrated videos:

```powershell
uv run clipsnstrips render-clips JOB_ID --vertical
uv run clipsnstrips render-art JOB_ID
```

Approve rights and output separately, then upload one artifact as private:

```powershell
uv run clipsnstrips approve JOB_ID rights REVIEWER "License and transformation reviewed"
uv run clipsnstrips approve JOB_ID output REVIEWER "Rendered output reviewed"
uv run clipsnstrips upload-private JOB_ID clips/SEGMENT.mp4 "Title" "Description"
```

Publishing requires one final decision:

```powershell
uv run clipsnstrips approve JOB_ID publish REVIEWER "Final channel publication approved"
uv run clipsnstrips publish JOB_ID YOUTUBE_VIDEO_ID
```

Use `uv run clipsnstrips show JOB_ID` at any stage to inspect state and audit records.

## Job artifacts

Each job lives in `output/<job-id>/`:

```text
manifest.json
source/
analysis/
clips/
art/
logs/
uploads/
```

Pipeline stages are resumable. Re-running rendering replaces deterministic output names
while updating checksums in the manifest.

## Development

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests mock network providers. The FFmpeg integration test creates a two-second synthetic
fixture and skips when FFmpeg is unavailable.
