[CmdletBinding()]
param(
    [string]$KbRoot,
    [string]$Python,
    [switch]$SkipDependencies
)

$ErrorActionPreference = 'Stop'
$utf8Encoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8Encoding
$OutputEncoding = $utf8Encoding
$env:PYTHONUTF8 = '1'

$pluginRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = $Python
if ([string]::IsNullOrWhiteSpace($pythonExecutable)) {
    $pythonExecutable = [Environment]::GetEnvironmentVariable('NV2_NUCLEAR_PYTHON')
}
if ([string]::IsNullOrWhiteSpace($pythonExecutable)) {
    $userProfilePath = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    if (-not [string]::IsNullOrWhiteSpace($userProfilePath)) {
        $bundledPython = Join-Path $userProfilePath '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $bundledPython -PathType Leaf) {
            $pythonExecutable = $bundledPython
        }
    }
}
if ([string]::IsNullOrWhiteSpace($pythonExecutable)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $pythonExecutable = $pythonCommand.Source
    }
}
if ([string]::IsNullOrWhiteSpace($pythonExecutable) -or -not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw 'Python 3.10+ was not found. Pass -Python or set NV2_NUCLEAR_PYTHON.'
}

$runner = Join-Path $PSScriptRoot 'run.py'
$requirements = Join-Path $PSScriptRoot 'requirements.txt'
if (-not $SkipDependencies) {
    & $pythonExecutable -m pip install --disable-pip-version-check -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw 'Python dependency installation failed.'
    }
}

if (-not [string]::IsNullOrWhiteSpace($KbRoot)) {
    & $pythonExecutable $runner configure $KbRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'Knowledge-base configuration failed.'
    }
    & $pythonExecutable $runner doctor
} else {
    & $pythonExecutable $runner doctor --allow-unconfigured --skip-integrity
    Write-Warning 'The plugin is installed, but the knowledge-base path is not configured. Run install.ps1 again with -KbRoot.'
}
exit $LASTEXITCODE
