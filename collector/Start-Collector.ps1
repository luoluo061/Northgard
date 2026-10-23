[CmdletBinding()]
param(
    [ValidateRange(0, 14400)][double]$Seconds,
    [ValidateRange(0.1, 10)][double]$Interval = 0.5,
    [string]$OutDir = "",
    [string]$PythonExecutable = "",
    [switch]$ResearchRaw
)
$ErrorActionPreference = "Stop"
function Get-CollectorSha256([string]$LiteralFilePath) {
    $stream = [IO.File]::OpenRead($LiteralFilePath)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose(); $stream.Dispose() }
}
$packageRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sumsPath = Join-Path $packageRoot "SHA256SUMS.txt"
$isSourceCheckout = Test-Path -LiteralPath (Join-Path $packageRoot ".git")
if (-not (Test-Path -LiteralPath $sumsPath -PathType Leaf) -and -not $isSourceCheckout) {
    throw "Release checksum list is missing. Use a verified release ZIP, or run the source with Python after reviewing it."
}
if (Test-Path -LiteralPath $sumsPath -PathType Leaf) {
$verifiedNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($line in Get-Content -LiteralPath $sumsPath -Encoding UTF8) {
    if ($line -notmatch '^([a-f0-9]{64})  (.+)$') { throw "Invalid release checksum line." }
    $expectedHash = $Matches[1]
    $relativeName = $Matches[2]
    if (-not $verifiedNames.Add($relativeName)) { throw "Duplicate release checksum entry." }
    if ([IO.Path]::IsPathRooted($relativeName) -or ($relativeName -split '[\\/]') -contains '..') {
        throw "Unsafe release member path."
    }
    $memberPath = [IO.Path]::GetFullPath((Join-Path $packageRoot $relativeName))
    if (-not $memberPath.StartsWith($packageRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release member escapes the package directory."
    }
    if (-not (Test-Path -LiteralPath $memberPath -PathType Leaf)) { throw "Required release file is missing: $relativeName" }
    if ((Get-CollectorSha256 $memberPath) -ne $expectedHash) {
        throw "Release checksum mismatch: $relativeName"
    }
}
if (-not $verifiedNames.Contains('release-manifest.json') -or -not $verifiedNames.Contains('collector/collect.py')) {
    throw "The release entry point and manifest must be covered by checksums."
}
$releaseManifest = Get-Content -LiteralPath (Join-Path $packageRoot 'release-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($releaseManifest.artifact_type -ne 'northgard_collector_source_release') { throw "Unexpected release manifest type." }
if ($verifiedNames.Count -ne (@($releaseManifest.files).Count + 1)) { throw "Manifest and checksum file inventories differ." }
foreach ($releaseFile in $releaseManifest.files) {
    if (-not $verifiedNames.Contains($releaseFile.path)) { throw "A manifest member is missing from checksums." }
    $releaseFilePath = Join-Path $packageRoot $releaseFile.path
    if ((Get-CollectorSha256 $releaseFilePath) -ne $releaseFile.sha256) {
        throw "Manifest checksum mismatch."
    }
}
}
$pythonCandidates = @()
if ($PythonExecutable) {
    $pythonCandidates += @{ Command = $PythonExecutable; Arguments = @() }
} else {
    $pythonCandidates += @{ Command = 'py'; Arguments = @('-3') }
    $pythonCandidates += @{ Command = 'python'; Arguments = @() }
    $pythonCandidates += @{ Command = 'python3'; Arguments = @() }
}
$pythonCommand = $null
$pythonPrefix = @()
$pythonCheckCode = 'import sys; print(int(sys.platform == bytes([119,105,110,51,50]).decode() and sys.maxsize > 2**32 and sys.version_info >= (3,11)))'
foreach ($candidate in $pythonCandidates) {
    if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) { continue }
    $candidateArgs = @($candidate.Arguments)
    try {
        $probe = & $candidate.Command @candidateArgs -c $pythonCheckCode 2>$null
        if ($LASTEXITCODE -eq 0 -and ($probe -join '').Trim() -eq '1') {
            $pythonCommand = $candidate.Command
            $pythonPrefix = $candidateArgs
            break
        }
    } catch { continue }
}
if (-not $pythonCommand) { throw "A local 64-bit Windows Python 3.11 or newer is required. No dependency downloads were attempted." }
if (-not $OutDir) {
    $OutDir = Join-Path $packageRoot ("captures\" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$captureArgs = @((Join-Path $PSScriptRoot 'collect.py'), '--interval', $Interval.ToString([Globalization.CultureInfo]::InvariantCulture), '--out-dir', $OutDir)
if ($PSBoundParameters.ContainsKey('Seconds')) {
    $captureArgs += @('--seconds', $Seconds.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if ($ResearchRaw) { $captureArgs += '--research-raw' }
Write-Host "Starting the local read-only collector. No time limit unless Seconds is supplied. Stop with Ctrl+C. No automatic upload."
& $pythonCommand @pythonPrefix @captureArgs
exit $LASTEXITCODE
