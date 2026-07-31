[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root

try {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run ruff format --check .
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & uv run ruff check .
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & uv run pytest
    }
    elseif (Test-Path ".venv\Scripts\python.exe") {
        & ".venv\Scripts\python.exe" -m ruff format --check .
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & ".venv\Scripts\python.exe" -m ruff check .
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & ".venv\Scripts\python.exe" -m pytest
    }
    else {
        throw "No project environment found. Run scripts\setup.ps1 first."
    }

    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
