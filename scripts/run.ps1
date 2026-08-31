param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('kb', 'assess', 'configure', 'doctor')]
    [string]$Action,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$utf8Encoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8Encoding
$OutputEncoding = $utf8Encoding
$env:PYTHONUTF8 = '1'

$pluginRoot = Split-Path -Parent $PSScriptRoot
$configuredPython = [Environment]::GetEnvironmentVariable('NV2_NUCLEAR_PYTHON')
$userProfilePath = [Environment]::GetEnvironmentVariable('USERPROFILE')

if ([string]::IsNullOrWhiteSpace($userProfilePath)) {
    $userProfilePath = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
}

if ([string]::IsNullOrWhiteSpace($userProfilePath)) {
    $userProfilePath = [Environment]::GetEnvironmentVariable('HOME')
}

if ([string]::IsNullOrWhiteSpace($userProfilePath)) {
    $homeDrive = [Environment]::GetEnvironmentVariable('HOMEDRIVE')
    $homePath = [Environment]::GetEnvironmentVariable('HOMEPATH')
    if ($homeDrive -and $homePath) {
        $userProfilePath = $homeDrive + $homePath
    }
}

$bundledPython = $null
if (-not [string]::IsNullOrWhiteSpace($userProfilePath)) {
    $bundledPython = Join-Path $userProfilePath '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}

$pythonCandidates = @()
if ($configuredPython) {
    $pythonCandidates += $configuredPython
}
if ($bundledPython) {
    $pythonCandidates += $bundledPython
}

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

$runner = Join-Path $pluginRoot 'scripts\run.py'
& $pythonExecutable $runner $Action @RemainingArgs
exit $LASTEXITCODE
