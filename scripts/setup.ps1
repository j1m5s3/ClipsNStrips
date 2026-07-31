[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root

try {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv sync --dev
    }
    elseif (Test-Path ".venv\Scripts\python.exe") {
        Write-Host "uv is not on PATH; bootstrapping it in the existing environment."
        & ".venv\Scripts\python.exe" -m ensurepip
        & ".venv\Scripts\python.exe" -m pip install uv
        & ".venv\Scripts\python.exe" -m uv sync --dev
    }
    else {
        throw "uv was not found. Install it from https://docs.astral.sh/uv/ and rerun this script."
    }

    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example; add your credentials before running live commands."
    }

    if (
        -not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or
        -not (Get-Command ffprobe -ErrorAction SilentlyContinue)
    ) {
        Write-Warning "FFmpeg/FFprobe are missing. On Windows: winget install --id Gyan.FFmpeg -e"
    }

    Write-Host "ClipsNStrips setup complete."
}
finally {
    Pop-Location
}
