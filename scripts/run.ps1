[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $CliArgs
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root

try {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run clipsnstrips @CliArgs
    }
    elseif (Test-Path ".venv\Scripts\python.exe") {
        & ".venv\Scripts\python.exe" -m clipsnstrips.cli @CliArgs
    }
    else {
        throw "Neither uv nor .venv\Scripts\python.exe was found. Run scripts\setup.ps1 first."
    }

    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
