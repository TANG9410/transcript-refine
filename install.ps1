[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidateSet("Codex", "Claude")]
    [string]$Target,
    [string]$DestinationRoot,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSCommandPath
$manifestPath = Join-Path $sourceRoot 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw 'Run install.ps1 from a cloned or extracted transcript-refine repository.'
}
$skillName = (Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json).name
if ($skillName -ne 'transcript-refine') {
    throw 'Unexpected skill name in manifest.json.'
}
$userProfile = [Environment]::GetFolderPath('UserProfile')
if (-not $DestinationRoot) {
    $DestinationRoot = if ($Target -eq 'Codex') {
        Join-Path $userProfile '.codex/skills'
    } else {
        Join-Path $userProfile '.claude/skills'
    }
}
$destination = [IO.Path]::GetFullPath((Join-Path $DestinationRoot $skillName))
$sourceResolved = [IO.Path]::GetFullPath($sourceRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
$sourcePrefix = $sourceResolved + [IO.Path]::DirectorySeparatorChar
if ($destination -eq $sourceResolved -or $destination.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The installation destination must be outside the source repository.'
}
if ((Test-Path -LiteralPath $destination) -and -not $Force) {
    throw "Target already exists: $destination. Review and back it up first; -Force overlays files without cleaning old files."
}
$rootFiles = @('SKILL.md', 'manifest.json', 'LICENSE', 'README.md', 'INSTALL.md', 'CONTRIBUTING.md', 'SECURITY.md', 'requirements-getnote.txt')
$resources = @{
    'agents' = @('.yaml', '.yml')
    'references' = @('.md')
    'scripts' = @('.py')
    'evals' = @('.json', '.md', '.txt')
}
$files = @($rootFiles | ForEach-Object { Get-Item -LiteralPath (Join-Path $sourceRoot $_) })
foreach ($folder in $resources.Keys) {
    $files += Get-ChildItem -LiteralPath (Join-Path $sourceRoot $folder) -Recurse -File |
        Where-Object {
            $_.Extension -in $resources[$folder] -and
            $_.FullName -notmatch '[\\/](?:__pycache__|\.test-tmp-root|outputs|\.workbuddy|\.transcript-refine)[\\/]' -and
            $_.Name -notin @('config.json', 'getnote-config.json')
        }
}
if ($PSCmdlet.ShouldProcess($destination, "Install $skillName for $Target")) {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($sourcePrefix.Length)
        $output = Join-Path $destination $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $output) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $output -Force
    }
    $installed = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $destination 'manifest.json') | ConvertFrom-Json
    Write-Host "Installed $($installed.name) $($installed.version) to $destination"
}
