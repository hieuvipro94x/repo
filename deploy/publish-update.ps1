param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$BuildOutput = "..\SX3 SCANER\bin\Release",

    [string]$OutputDirectory = ".\server-files\updates",

    [string]$Notes = "Cap nhat SX3 Scanner"
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot $BuildOutput))
$output = [System.IO.Path]::GetFullPath((Join-Path $scriptRoot $OutputDirectory))
$stage = Join-Path $env:TEMP ("sx3-publish-" + [System.Guid]::NewGuid().ToString("N"))
$packageName = "sx3-scanner-$Version.zip"
$packagePath = Join-Path $output $packageName

if (-not (Test-Path -LiteralPath (Join-Path $source "SX3 SCANER.exe"))) {
    throw "Khong tim thay ban Release tai: $source. Hay build Release truoc."
}

New-Item -ItemType Directory -Path $output -Force | Out-Null
New-Item -ItemType Directory -Path $stage -Force | Out-Null

try {
    Get-ChildItem -LiteralPath $source -File -Recurse |
        Where-Object {
            $_.Name -notin @("database.db", "database.db-wal", "database.db-shm", "product.db", "product.db-wal", "product.db-shm") -and
            $_.Extension -notin @(".pdb", ".xml")
        } |
        ForEach-Object {
            $relative = $_.FullName.Substring($source.Length).TrimStart("\")
            $destination = Join-Path $stage $relative
            $folder = Split-Path -Parent $destination
            New-Item -ItemType Directory -Path $folder -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path -LiteralPath $packagePath) {
        Remove-Item -LiteralPath $packagePath -Force
    }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $packagePath)

    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $packagePath).Hash
    $manifest = [ordered]@{
        version = $Version
        package = $packageName
        sha256 = $hash
        notes = $Notes
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output "manifest.json") -Encoding UTF8

    Write-Host "Da tao goi cap nhat:"
    Write-Host "  $packagePath"
    Write-Host "  $(Join-Path $output 'manifest.json')"
}
finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
