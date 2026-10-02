$ErrorActionPreference = 'Stop'

$testRoot = Split-Path -Parent $PSScriptRoot
$sourceRoot = Join-Path $testRoot 'sources'
$baselineFile = Join-Path $testRoot 'baselines.json'
$baselines = Get-Content -LiteralPath $baselineFile -Raw | ConvertFrom-Json

New-Item -ItemType Directory -Path $sourceRoot -Force | Out-Null

foreach ($baseline in $baselines) {
    $folderName = $baseline.repository -replace '/', '__'
    $target = Join-Path $sourceRoot $folderName
    $remote = "https://github.com/$($baseline.repository).git"

    if (-not (Test-Path -LiteralPath $target)) {
        git clone --filter=blob:none --no-checkout $remote $target
    }
    if (-not (Test-Path -LiteralPath (Join-Path $target '.git'))) {
        throw "Target exists but is not a Git repository: $target"
    }

    git -C $target fetch origin $baseline.sha --depth=1
    git -C $target checkout --detach $baseline.sha
    $actual = (git -C $target rev-parse HEAD).Trim()
    if ($actual -ne $baseline.sha) {
        throw "Revision mismatch for $($baseline.repository): $actual"
    }
    Write-Host "$($baseline.repository) -> $actual"
}
