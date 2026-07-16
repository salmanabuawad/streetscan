# Deploy Buqata StreetScan to scan.kortexd.com (185.229.226.37).
# Run from repo root: .\deploy\deploy-to-server.ps1
# Optional: -SkipBuild  reuse the existing frontend/dist

param([switch]$SkipBuild)

$REMOTE = "root@185.229.226.37"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir

function Get-DeployBash {
    foreach ($p in @(
        "C:\Program Files\Git\bin\bash.exe",
        "C:\Program Files (x86)\Git\bin\bash.exe"
    )) {
        if (Test-Path $p) { return $p }
    }
    return "bash"
}
$BashExe = Get-DeployBash

if (-not $SkipBuild) {
    Write-Host "Building frontend..." -ForegroundColor Yellow
    Push-Location "$repoRoot\frontend"
    npm run build
    $buildOk = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not $buildOk) { Write-Host "Frontend build failed." -ForegroundColor Red; exit 1 }
}

Write-Host "Uploading to ${REMOTE}..." -ForegroundColor Yellow
$driveLetter = $repoRoot.Substring(0,1).ToLower()
$repoRootUnix = '/' + $driveLetter + ($repoRoot.Substring(2) -replace '\\', '/')
& $BashExe -c "ssh $REMOTE 'rm -rf /tmp/streetscan-stage && mkdir -p /tmp/streetscan-stage' && tar -czf - --exclude='*/node_modules' --exclude='*/.venv' --exclude='*/__pycache__' --exclude='backend/uploads' -C '$repoRootUnix' backend frontend/dist deploy scripts | ssh $REMOTE 'tar -xzf - -C /tmp/streetscan-stage'"
if ($LASTEXITCODE -ne 0) { Write-Host "Upload failed." -ForegroundColor Red; exit 1 }

Write-Host "Running server setup..." -ForegroundColor Yellow
ssh $REMOTE "sed -i 's/\r//' /tmp/streetscan-stage/deploy/setup-server.sh && bash /tmp/streetscan-stage/deploy/setup-server.sh"
if ($LASTEXITCODE -ne 0) { Write-Host "Server setup failed." -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "=== Deploy done: https://scan.kortexd.com ===" -ForegroundColor Green
