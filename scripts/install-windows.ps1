# VoiceStudio: one-shot LAN install on Windows with Docker Desktop (+ NVIDIA GPU).
#
#   irm https://raw.githubusercontent.com/Clawdio900/VoiceStudio/webapp-server/scripts/install-windows.ps1 | iex
#   (or from a checkout:  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1)
#
# Run in PowerShell AS ADMINISTRATOR. It installs what is missing via winget:
# WSL 2 (one reboot the first time, then run it again), Git for Windows and
# Docker Desktop, then builds and starts VoiceStudio. The only thing you
# provide is a current NVIDIA driver for GPU mode. Safe to re-run: it updates
# in place and keeps your data and API key. No checks are bypassed: RAM preflight stays on and no
# low-memory preset is applied.
#
# Optional environment overrides (set before running):
#   $env:VS_INSTALL_DIR  (default $env:USERPROFILE\VoiceStudio)
#   $env:VS_PORT         (default 9999)
#   $env:VS_LAN_IP       (default: auto-detected)
#   $env:VS_FORCE_CPU=1  (skip the GPU)
#   $env:HF_TOKEN        (optional, gated Hugging Face models)

# 'Continue': Windows PowerShell 5.1 turns redirected native stderr into
# terminating errors under 'Stop'. Native exit codes are checked explicitly.
$ErrorActionPreference = 'Continue'
$RepoUrl    = 'https://github.com/Clawdio900/VoiceStudio.git'
$Branch     = 'webapp-server'
$InstallDir = if ($env:VS_INSTALL_DIR) { $env:VS_INSTALL_DIR } else { Join-Path $env:USERPROFILE 'VoiceStudio' }
$Port       = if ($env:VS_PORT) { $env:VS_PORT } else { '9999' }

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
# throw, not exit: under `irm | iex` an exit would close the PowerShell window.
function Die($m)  { Write-Host "xx  $m" -ForegroundColor Red; throw "VoiceStudio install failed: $m" }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Die 'Run PowerShell as Administrator (right-click -> Run as administrator).' }

# ── 1. Prerequisites (installed automatically) ─────────────────────────────
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Winget-Install($id, $label) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Die "winget is missing. Install 'App Installer' from the Microsoft Store, then re-run (needed to install $label)."
    }
    Say "Installing $label"
    winget install -e --id $id --silent --accept-package-agreements --accept-source-agreements
    # -1978335189 / -1978335135: no applicable update / already installed
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189 -and $LASTEXITCODE -ne -1978335135) {
        Die "Installing $label failed (winget exit $LASTEXITCODE)."
    }
    Refresh-Path
}

# WSL 2 (Docker Desktop's engine). A fresh enable needs a reboot.
wsl --status *> $null
if ($LASTEXITCODE -ne 0) {
    Say 'Enabling WSL 2'
    wsl --install --no-distribution
    Warn 'WSL 2 was just enabled. RESTART Windows, then run this same command again to continue.'
    return
}
wsl --update *> $null

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Winget-Install 'Git.Git' 'Git for Windows' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $gitExe = Join-Path $env:ProgramFiles 'Git\cmd'
    if (Test-Path $gitExe) { $env:Path += ";$gitExe" } else { Die 'Git is still not available. Open a new PowerShell window and re-run.' }
}

$dockerDesktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
if (-not (Test-Path $dockerDesktop)) { Winget-Install 'Docker.DockerDesktop' 'Docker Desktop' }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $dockerBin = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin'
    if (Test-Path $dockerBin) { $env:Path += ";$dockerBin" }
}
if (-not (Test-Path $dockerDesktop)) { Die 'Docker Desktop did not install. Install it from https://www.docker.com/products/docker-desktop/ and re-run.' }

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Say 'Starting Docker Desktop (accept its terms if a window asks; first start can take a few minutes)'
    Start-Process $dockerDesktop
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    }
    if (-not $ready) {
        Die 'Docker Desktop did not start. Open it, finish its first-run screens until "Engine running", then re-run. If Windows asks you to sign out (docker-users group), do that first.'
    }
}
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Die 'docker compose is not available. Update Docker Desktop.' }

# Start Docker Desktop at sign-in so VoiceStudio comes back after a reboot.
# (settings-store.json uses "AutoStart"; the older settings.json "autoStart".)
foreach ($pair in @(@('settings-store.json', 'AutoStart'), @('settings.json', 'autoStart'))) {
    $settings = Join-Path $env:APPDATA ('Docker\' + $pair[0])
    if (-not (Test-Path $settings)) { continue }
    try {
        $json = Get-Content $settings -Raw | ConvertFrom-Json
        $json | Add-Member -NotePropertyName $pair[1] -NotePropertyValue $true -Force
        # No BOM: Docker Desktop's JSON reader rejects one.
        [IO.File]::WriteAllText($settings, ($json | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false))
    } catch { Warn 'Could not enable Docker Desktop auto-start; turn on "Start Docker Desktop when you sign in" in its settings.' }
    break
}

# Memory Docker (WSL 2) may use. VoiceStudio wants >= 8 GB.
$dockerMemGb = [math]::Round(([double](docker info --format '{{.MemTotal}}')) / 1GB, 1)
Say "Docker can use $dockerMemGb GB of RAM"
if ($dockerMemGb -lt 8) {
    Warn "Docker is limited to $dockerMemGb GB. Raise it: create $env:USERPROFILE\.wslconfig with"
    Warn "  [wsl2]`n  memory=16GB"
    Warn "then run 'wsl --shutdown', restart Docker Desktop, and re-run this script."
}

# ── 2. GPU ─────────────────────────────────────────────────────────────────
$UseGpu = $env:VS_FORCE_CPU -ne '1'
if ($UseGpu) {
    Say 'Checking NVIDIA GPU'
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        Die 'nvidia-smi not found. Install the latest NVIDIA driver, or set $env:VS_FORCE_CPU=1 for CPU mode.'
    }
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader | ForEach-Object { "    GPU: $_" }
    $cap = (nvidia-smi --query-gpu=compute_cap --format=csv,noheader | Select-Object -First 1).Trim()
    if ($cap -and [double]::Parse($cap, [Globalization.CultureInfo]::InvariantCulture) -lt 7.0) {
        Die "GPU compute capability $cap is below 7.0 and unsupported by the bundled PyTorch. Set `$env:VS_FORCE_CPU=1 to run on CPU."
    }
    Say 'Testing GPU access from a container'
    docker run --rm --gpus all ubuntu:24.04 nvidia-smi -L
    if ($LASTEXITCODE -ne 0) {
        Die 'Docker cannot reach the GPU. Update the NVIDIA driver and Docker Desktop, and make sure the WSL 2 backend is enabled.'
    }
}

# ── 3. Source ──────────────────────────────────────────────────────────────
if (Test-Path (Join-Path $InstallDir '.git')) {
    Say "Updating $InstallDir"
    git -C $InstallDir fetch --depth 1 origin $Branch
    if ($LASTEXITCODE -ne 0) { Die 'git fetch failed.' }
    git -C $InstallDir checkout -q -B $Branch FETCH_HEAD
} else {
    Say "Cloning VoiceStudio into $InstallDir"
    # LF line endings: the files are built into a Linux image.
    git -c core.autocrlf=false clone --depth 1 -b $Branch $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Die 'git clone failed.' }
    git -C $InstallDir config core.autocrlf false
}
Set-Location $InstallDir

# ── 4. Configuration ───────────────────────────────────────────────────────
$EnvFile = Join-Path $InstallDir 'deploy\.env'
if (-not (Test-Path $EnvFile)) {
    $ip = $env:VS_LAN_IP
    if (-not $ip) {
        $ip = Get-NetIPConfiguration |
            Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.InterfaceAlias -notmatch 'vEthernet|WSL|Hyper-V|VPN|Tailscale|WireGuard' } |
            ForEach-Object { $_.IPv4Address.IPAddress } | Select-Object -First 1
    }
    if (-not $ip) { Die 'Could not detect the LAN IP. Set $env:VS_LAN_IP="192.168.x.y" and re-run.' }
    Say "Writing deploy\.env (LAN IP $ip, port $Port)"
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $key = [Convert]::ToBase64String($bytes)
    $content = "VOICESTUDIO_HOST=$ip`nVOICESTUDIO_PORT=$Port`nOMNIVOICE_API_KEY=$key`nHF_TOKEN=$($env:HF_TOKEN)`n"
    # LF endings, no BOM: docker compose reads this file.
    [IO.File]::WriteAllText($EnvFile, $content, (New-Object Text.UTF8Encoding $false))
} else {
    Say 'Keeping existing deploy\.env'
}
$envMap = @{}
Get-Content $EnvFile | Where-Object { $_ -match '^\s*([A-Z0-9_]+)=(.*)$' } | ForEach-Object {
    $null = $_ -match '^\s*([A-Z0-9_]+)=(.*)$'; $envMap[$Matches[1]] = $Matches[2].Trim()
}
$HostIp = $envMap['VOICESTUDIO_HOST']
$Port   = if ($envMap['VOICESTUDIO_PORT']) { $envMap['VOICESTUDIO_PORT'] } else { $Port }
$ApiKey = $envMap['OMNIVOICE_API_KEY']

# ── 5. Firewall ────────────────────────────────────────────────────────────
$ruleName = "VoiceStudio $Port"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    Say "Allowing inbound TCP $Port on private networks"
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port -Profile Private -Action Allow -ErrorAction Stop | Out-Null
}

# ── 6. Build and start ─────────────────────────────────────────────────────
$compose = @('compose', '-f', 'deploy/docker-compose.lan.yml')
if ($UseGpu) { $compose += @('-f', 'deploy/docker-compose.server.gpu.yml') }
Say 'Building and starting VoiceStudio (first build takes 10-30 minutes)'
& docker @compose up -d --build
if ($LASTEXITCODE -ne 0) { Die 'docker compose up failed. See the output above.' }

$url = "https://${HostIp}:$Port"
Say 'Waiting for VoiceStudio to become healthy (first start can take several minutes)'
$status = 'is still starting (check the logs below)'
for ($i = 0; $i -lt 120; $i++) {
    & curl.exe -ksf "$url/health" *> $null
    if ($LASTEXITCODE -eq 0) { $status = 'is running'; break }
    Start-Sleep -Seconds 10
}

$composeCmd = 'docker ' + ($compose -join ' ')
Write-Host ''
Write-Host '────────────────────────────────────────────────────────────────'
Write-Host " VoiceStudio $status"
Write-Host ''
Write-Host "   Open:     $url"
Write-Host "   API key:  $ApiKey"
Write-Host "             (also in $EnvFile)"
Write-Host ''
Write-Host ' The browser warns about the certificate once per device;'
Write-Host ' choose "Advanced -> Proceed".'
Write-Host ''
Write-Host " Logs:     cd $InstallDir; $composeCmd logs -f voicestudio"
Write-Host " Stop:     cd $InstallDir; $composeCmd down"
Write-Host ' Update:   re-run this script'
Write-Host '────────────────────────────────────────────────────────────────'
