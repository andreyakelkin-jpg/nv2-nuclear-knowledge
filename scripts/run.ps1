param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('kb', 'assess', 'configure')]
    [string]$Action,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$utf8Encoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8Encoding
$OutputEncoding = $utf8Encoding
$env:PYTHONUTF8 = '1'

$pluginRoot = Split-Path -Parent $PSScriptRoot
$userProfilePath = [Environment]::GetFolderPath('UserProfile')
$bundledPython = Join-Path $userProfilePath '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$configuredPython = [Environment]::GetEnvironmentVariable('NV2_NUCLEAR_PYTHON')

$pythonCandidates = @()
if ($configuredPython) {
    $pythonCandidates += $configuredPython
}
$pythonCandidates += $bundledPython

$pythonExecutable = $null
foreach ($candidate in $pythonCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        $pythonExecutable = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}

if (-not $pythonExecutable) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $pythonExecutable = $pythonCommand.Source
    }
}

if (-not $pythonExecutable) {
    throw 'Python was not found. Install Python 3 or set NV2_NUCLEAR_PYTHON.'
}

$scripts = @{
    kb        = Join-Path $pluginRoot 'scripts\kb.py'
    assess    = Join-Path $pluginRoot 'scripts\assess_training.py'
    configure = Join-Path $pluginRoot 'scripts\configure.py'
}

& $pythonExecutable $scripts[$Action] @RemainingArgs
exit $LASTEXITCODE
