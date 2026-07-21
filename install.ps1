[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidateSet("Codex", "Claude")]
    [string]$Target,
    [string]$DestinationRoot,
    [switch]$Force
)

$sourceRoot = Split-Path -Parent $PSCommandPath
$manifestPath = Join-Path $sourceRoot "manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Run install.ps1 from a cloned transcript-refine-v3 repository."
}
$skillName = (Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath | ConvertFrom-Json).name
$userProfile = [Environment]::GetFolderPath("UserProfile")
if (-not $DestinationRoot) {
    $DestinationRoot = if ($Target -eq "Codex") {
        Join-Path $userProfile ".codex\skills"
    } else {
        Join-Path $userProfile ".claude\skills"
    }
}
$destination = Join-Path $DestinationRoot $skillName
if ((Test-Path -LiteralPath $destination) -and -not $Force) {
    throw "Target already exists: $destination. Review it first; use -Force only to overlay the cloned package."
}
if ($PSCmdlet.ShouldProcess($destination, "Install $skillName for $Target")) {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Get-ChildItem -LiteralPath $sourceRoot -Force |
        Where-Object { $_.Name -notin @(".git", ".github", "outputs", ".workbuddy") } |
        Copy-Item -Destination $destination -Recurse -Force
    Write-Host "Installed $skillName to $destination"
}