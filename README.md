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

## No-approval processing mode

Use `--no-approval` only when you intentionally want to bypass ingest and span-review gates.
Every bypass is recorded in the job manifest with `bypassed: true`. This mode never bypasses
the rights/output approvals required for private upload or the separate public-publish
approval.

Run a full processing pipeline from a YouTube URL or local file:

```powershell
uv run clipsnstrips run-e2e "https://www.youtube.com/watch?v=VIDEO_ID" `
  --no-approval --vertical --art

uv run clipsnstrips run-e2e C:\media\source.mp4 --no-approval --vertical
```

`run-e2e` refuses to start unless `--no-approval` is supplied. It selects all valid candidate
spans automatically. Individual processing stages also accept the flag:

```powershell
uv run clipsnstrips ingest-local JOB_ID C:\media\source.mp4 --no-approval
uv run clipsnstrips analyze JOB_ID --no-approval
uv run clipsnstrips render-clips JOB_ID --no-approval --vertical
uv run clipsnstrips render-art JOB_ID --no-approval
```

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

For a guided single-URL workflow, pass the YouTube URL directly:

```powershell
uv run clipsnstrips process-youtube "https://www.youtube.com/watch?v=VIDEO_ID" `
  --reviewer "Your Name" --vertical
```

The command fetches metadata and risk reasons, then pauses for an authorization attestation
before downloading. It pauses again for manual segment selection and contextual review before
rendering. Add `--art` to generate paid AI artwork. Upload and publication remain separate
approval-gated commands.

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
uv run clipsnstrips render-art JOB_ID --segment-id SEGMENT_ID
```

Highlight candidate count scales with source duration using
`HIGHLIGHT_SECONDS_PER_CANDIDATE`, bounded by `HIGHLIGHT_MIN_CANDIDATES` and
`HIGHLIGHT_MAX_CANDIDATES`.

Art rendering creates approximately one panel per `ART_SECONDS_PER_PANEL` seconds, bounded
by `ART_MIN_PANELS` and `ART_MAX_PANELS`. Each panel uses its matching transcript excerpt
and a source frame from the same timestamp. Gemini analyzes the segment frames into a
segment-scoped subject bible; OpenAI then uses multi-image editing rather than text-only
generation. Later panels also receive the preceding generated panel, preserving the comic
style without carrying visual identity between unrelated segments.

Reference behavior is configurable:

```dotenv
SCENE_CONTEXT_MODEL=gemini-3.6-flash
OPENAI_IMAGE_MODEL=gpt-image-1
OPENAI_IMAGE_FIDELITY=high
ART_MODERATION_FALLBACK_ENABLED=true
ART_MODERATION_FINAL_ACTION=placeholder
REFERENCE_FRAME_COUNT=6
REFERENCE_FRAME_MAX_WIDTH=1024
```

High-fidelity multi-image edits and scene analysis are paid API calls. References, context,
and completed panels are reused only while the source checksum, timestamps, models, prompts,
and reference checksums match. Changing any of them invalidates the affected segment cache.
Each completed panel is checkpointed immediately so an interrupted run can resume.

### Image moderation fallback

An OpenAI `moderation_blocked` response is not bypassed or repeatedly reworded. The renderer
uses a fixed, bounded benign-transformation sequence:

1. Stop the original request.
2. Try a deterministic non-explicit prompt without transcript, sensitive action, hook, or
   narrative text while retaining safe visual references.
3. If the input images are also rejected, try the same benign prompt without any images.
4. If that is rejected, create a local neutral placeholder or fail, according to
   `ART_MODERATION_FINAL_ACTION`.

The fallback records only the moderation category, stage, provider request ID, reference
count, and final disposition. Raw provider errors and additional sensitive prompt text are
not persisted. Authentication, quota, invalid-request, and network errors are never treated
as moderation blocks and are not retried by this mechanism.

Set `ART_MODERATION_FALLBACK_ENABLED=false` to fail immediately on the first moderation
block. Set `ART_MODERATION_FINAL_ACTION=fail` to disable the neutral placeholder after the
two benign attempts. Changing either setting invalidates the affected panel cache. Safe
fallback requests can incur additional image-generation charges.

Panels, audit metadata, and the finished illustrated video are grouped together:

```text
output/<job-id>/art/<segment-id>/
  references/
    references.json
    panel-01-....jpg
    representative-01-....jpg
  scene-context.json
  generation.json
  panel-01.png
  panel-02.png
  prompts.json
  panel-video.mp4
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
<condensed-video-title>_<condensed-channel-title>_<UTC-timestamp>
```

Title components are lowercase ASCII letters and numbers with spaces and punctuation removed,
and are capped at 48 characters each. Example:

```text
thewallet2021officialtrailer_thetimdillonshow_20260731T212233123456Z
```

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
