[CmdletBinding()]
param([switch]$WhatIf)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$baseline = Join-Path $PSScriptRoot 'baseline'
$files = @('tam\web\app.js','tam\web\index.src.html','tam\web\styles.css','tam\web\index.html')
foreach ($relative in $files) {
  $src = Join-Path $baseline ([IO.Path]::GetFileName($relative))
  $dst = Join-Path $root $relative
  if (-not (Test-Path -LiteralPath $src)) { throw "Missing baseline: $src" }
  if (-not (Test-Path -LiteralPath (Split-Path -Parent $dst))) { New-Item -ItemType Directory -Force (Split-Path -Parent $dst) | Out-Null }
  Copy-Item -LiteralPath $src -Destination $dst -Force -WhatIf:$WhatIf
}
if ($WhatIf) { Write-Output 'Rollback preview complete (no files changed).' }
else { Write-Output 'Rollback complete: restored four frontend files from baseline.' }
