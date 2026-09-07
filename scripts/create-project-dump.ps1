[CmdletBinding()]
param(
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot ("dumps\gosradar-{0}.zip" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
} elseif (-not [IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $projectRoot $OutputPath
}

$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if ([IO.Path]::GetExtension($OutputPath) -ne ".zip") {
    throw "OutputPath must end with .zip"
}
if (Test-Path -LiteralPath $OutputPath) {
    throw "File already exists: $OutputPath"
}

$outputDirectory = Split-Path -Parent $OutputPath
[IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
$excludedDirectories = @(".git", ".venv", "node_modules", "dist", ".vite", ".pytest_cache", "__pycache__", "dumps")
$excludedFiles = @(".env")
$rootPrefix = $projectRoot.TrimEnd("\") + "\"
$files = Get-ChildItem -LiteralPath $projectRoot -File -Recurse | Where-Object {
    $relative = $_.FullName.Substring($rootPrefix.Length)
    $parts = $relative -split "[\\/]"
    -not ($parts | Where-Object { $excludedDirectories -contains $_ -or $_ -like "*.egg-info" }) -and
    $excludedFiles -notcontains $_.Name -and
    $_.Extension -notin @(".db", ".pyc", ".tsbuildinfo")
}

$temporaryPath = "$OutputPath.partial"
if (Test-Path -LiteralPath $temporaryPath) {
    throw "Temporary file already exists: $temporaryPath"
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
try {
    $stream = [IO.File]::Open($temporaryPath, [IO.FileMode]::CreateNew)
    $archive = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($rootPrefix.Length).Replace("\", "/")
            $entry = $archive.CreateEntry($relative, [IO.Compression.CompressionLevel]::Optimal)
            $source = [IO.File]::OpenRead($file.FullName)
            $target = $entry.Open()
            try { $source.CopyTo($target) } finally { $target.Dispose(); $source.Dispose() }
        }
    } finally {
        $archive.Dispose()
        $stream.Dispose()
    }

    $check = [IO.Compression.ZipFile]::OpenRead($temporaryPath)
    try {
        $names = @($check.Entries.FullName)
        if ($names -notcontains "README.md" -or $names -notcontains "scripts/create-project-dump.ps1") {
            throw "Dump validation failed: required files are missing"
        }
        if ($names -contains ".env" -or @($names | Where-Object { $_ -match "(^|/)node_modules/|(^|/)\.venv/|(^|/)\.git/" }).Count -gt 0) {
            throw "Dump validation failed: secrets or dependencies were included"
        }
    } finally {
        $check.Dispose()
    }
    [IO.File]::Move($temporaryPath, $OutputPath)
} catch {
    if (Test-Path -LiteralPath $temporaryPath) {
        [IO.File]::Delete($temporaryPath)
    }
    throw
}

$result = Get-Item -LiteralPath $OutputPath
[pscustomobject]@{
    Path = $result.FullName
    Files = $files.Count
    SizeBytes = $result.Length
    Sha256 = (Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256).Hash
}
