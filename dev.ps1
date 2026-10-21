<#
.SYNOPSIS
  PowerShell twin of the Makefile (Windows has no make). Usage: .\dev.ps1 <target>
.EXAMPLE
  .\dev.ps1 up
  .\dev.ps1 logs redpanda
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'setup', 'up', 'down', 'ps', 'logs', 'smoke', 'ingest', 'fmt', 'lint', 'test', 'verify', 'clean')]
    [string]$Target = 'help',

    # logs only: limit to one service, e.g. `.\dev.ps1 logs redpanda`
    [Parameter(Position = 1)]
    [string]$Service = ''
)

$ErrorActionPreference = 'Stop'
$ProjectDir = Join-Path $PSScriptRoot 'projects/01-chain-risk-copilot'

# Run a native command in the project directory; stop on a non-zero exit.
function Invoke-Step {
    param([string]$Exe, [string[]]$Arguments)
    Push-Location $ProjectDir
    try {
        # Native tools (docker) write progress to stderr; with 'Stop', PS 5.1 turns that into an
        # error whenever the caller redirects it. Exit codes are checked explicitly below.
        # Resolve first so a missing tool throws regardless of the preference; otherwise
        # LASTEXITCODE stays 0 and the step would "pass" having run nothing.
        $resolved = (Get-Command $Exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = $null
        & $resolved @Arguments
        # $null means the process never ran (no exit code was set): fail, never pass.
        if ($LASTEXITCODE -ne 0) { throw "'$Exe $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
    }
    finally { Pop-Location }
}

# One definition of each step list, so `verify` can never drift from `lint` and `test`.
# `@(,x)` keeps a single step from being flattened into a plain string array.
$LintSteps = @(@('ruff', 'check', '.'), @('ruff', 'format', '--check', '.'), @('mypy'))
$TestSteps = @(, @('pytest'))
$FmtSteps  = @(@('ruff', 'format', '.'), @('ruff', 'check', '--fix', '.'))

function Invoke-Steps {
    param([object[]]$Steps)
    foreach ($s in $Steps) { Invoke-Step 'uv' (@('run') + $s) }
}

function Invoke-Compose {
    param([string[]]$Arguments)
    Invoke-Step 'docker' (@('compose', '-f', 'infra/docker-compose.yml') + $Arguments)
}

switch ($Target) {
    'help' {
        Write-Host @'
  help     List the targets
  setup    Create infra/.env if absent, uv sync --all-groups, install pre-commit hooks
  up       Start the stack and wait until every service is healthy
  down     Stop the stack, KEEP data
  ps       Show service status
  logs     Follow logs (all services, or: .\dev.ps1 logs redpanda)
  smoke    Run the end-to-end smoke test against the running stack
  ingest   Ingest the 7-day window into bronze (needs the stack up)
  fmt      Format and auto-fix with ruff
  lint     ruff check, ruff format --check, mypy
  test     Unit tests (no running stack needed)
  verify   lint + test: the pre-push check
  clean    DESTROYS DATA: stop the stack and delete its volumes (down -v)
'@
    }
    'setup' {
        $envFile = Join-Path $ProjectDir 'infra/.env'
        if (Test-Path $envFile) {
            Write-Host 'infra/.env exists - left untouched'
        }
        else {
            Copy-Item (Join-Path $ProjectDir 'infra/.env.example') $envFile
            Write-Host 'created infra/.env from .env.example'
        }
        Invoke-Step 'uv' @('sync', '--all-groups')
        Invoke-Step 'uv' @('run', 'pre-commit', 'install')
    }
    'up'    {
        # `up --wait` counts the one-shot minio-init exiting (0) as a failure, so wait on the
        # long-running services only, then run minio-init separately and use its exit code.
        Invoke-Compose @('up', '-d', '--wait', 'redpanda', 'redpanda-console', 'minio', 'postgres-ops')
        Invoke-Compose @('up', '--no-deps', '--exit-code-from', 'minio-init', 'minio-init')
    }
    'down'  { Invoke-Compose @('down') }
    'ps'    { Invoke-Compose @('ps') }
    'logs'  {
        $a = @('logs', '-f', '--tail=100')
        if ($Service) { $a += $Service }
        Invoke-Compose $a
    }
    'smoke' { Invoke-Step 'uv' @('run', 'python', 'scripts/smoke_test.py') }
    'ingest' { Invoke-Step 'uv' @('run', 'python', '-m', 'chainrisk.ingest.bronze') }
    'fmt'   { Invoke-Steps $FmtSteps }
    'lint'  { Invoke-Steps $LintSteps }
    'test'  { Invoke-Steps $TestSteps }
    'verify' { Invoke-Steps ($LintSteps + $TestSteps) }
    'clean' { Invoke-Compose @('down', '-v') }
}
