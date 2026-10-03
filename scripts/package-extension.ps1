$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$extensionPath = Join-Path $repoPath 'extension'
$artifactPath = Join-Path $repoPath 'dist'
New-Item -ItemType Directory -Force -Path $artifactPath | Out-Null
$files = @('manifest.json', 'background.js', 'core.mjs', 'export.html', 'export.css', 'export.js', 'README.md', 'PRIVACY.md')
$sources = $files | ForEach-Object { Join-Path $extensionPath $_ }
$archive = Join-Path $artifactPath 'channelmind-extension.zip'
Compress-Archive -LiteralPath $sources -DestinationPath $archive -Force
Write-Output "Extension package: $archive"
