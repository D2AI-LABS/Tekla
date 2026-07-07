# Start TeklaExtractor (single instance). Requires Tekla Structures open + model connected.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$proj = Join-Path $root "TeklaExtractor\TeklaExtractor.csproj"
$exe  = Join-Path $root "TeklaExtractor\bin\Debug\net48\TeklaExtractor.exe"

$running = Get-Process -Name "TeklaExtractor" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "Stopping $($running.Count) existing TeklaExtractor process(es)..."
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

Write-Host "Building..."
dotnet build $proj --configuration Debug
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Starting TeklaExtractor..."
Set-Location (Split-Path $exe)
& $exe
