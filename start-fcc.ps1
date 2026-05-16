# start-fcc.ps1
param([string]$ProjectDir)

$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

if (-not $ProjectDir -or $ProjectDir.Trim() -eq "") {
    $ProjectDir = $PSScriptRoot
}
$ProjectDir = $ProjectDir.Trim().Trim('"').Trim("'")
$ScriptDir  = $PSScriptRoot

$settingsPath = "$env:USERPROFILE\.claude\settings.json"
$backupPath   = "$env:USERPROFILE\.claude\settings.json.mimo-backup"
$settingsDir  = "$env:USERPROFILE\.claude"

# Startup check: auto-restore if last session crashed
if (Test-Path $backupPath) {
    $nimRunning = Get-NetTCPConnection -LocalPort 8082 -ErrorAction SilentlyContinue
    if (-not $nimRunning) {
        Write-Host "Detected abnormal exit, restoring mimo config..." -ForegroundColor Yellow
        Copy-Item $backupPath $settingsPath -Force
        Remove-Item $backupPath -Force
        Write-Host "mimo config restored" -ForegroundColor Green
    }
}

$procs = @()

# Function: restore mimo config and stop services
function Restore-Mimo {
    if (Test-Path $backupPath) {
        Copy-Item $backupPath $settingsPath -Force
        Remove-Item $backupPath -Force
        Write-Host "`nmimo config restored" -ForegroundColor Cyan
    }
    if ($procs.Count -gt 0) {
        Write-Host "Stopping services..." -ForegroundColor Yellow
        foreach ($p in $procs) {
            if (-not $p.HasExited) {
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            }
        }
        Write-Host "All services stopped" -ForegroundColor Green
    }
}

try {
    # 1. Key proxy (8083)
    $keyProxyRunning = Get-NetTCPConnection -LocalPort 8083 -ErrorAction SilentlyContinue
    if (-not $keyProxyRunning) {
        Write-Host "Starting key proxy..." -ForegroundColor Yellow
        $p = Start-Process -FilePath "python" `
            -ArgumentList "`"$ScriptDir\nim_key_proxy.py`"" `
            -WorkingDirectory $ScriptDir -PassThru
        $procs += $p
        Start-Sleep -Seconds 2
    } else {
        Write-Host "Key proxy already running: http://127.0.0.1:8083" -ForegroundColor Green
    }

    # 2. fcc-server (8082)
    $fccRunning = Get-NetTCPConnection -LocalPort 8082 -ErrorAction SilentlyContinue
    if (-not $fccRunning) {
        Write-Host "Starting fcc-server..." -ForegroundColor Yellow
        $p = Start-Process -FilePath "fcc-server" -WindowStyle Hidden -PassThru
        $procs += $p
        Start-Sleep -Seconds 4
        Write-Host "Proxy ready: http://127.0.0.1:8082" -ForegroundColor Green
    } else {
        Write-Host "Proxy already running: http://127.0.0.1:8082" -ForegroundColor Green
    }

    # 3. Backup settings.json and switch to NIM mode
    if (-not (Test-Path $settingsDir)) {
        New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null
    }
    if (-not (Test-Path $settingsPath)) {
        '{"env":{}}' | Set-Content $settingsPath -Encoding UTF8
    }
    Copy-Item $settingsPath $backupPath -Force

    $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
    if (-not $settings.PSObject.Properties['env']) {
        $settings | Add-Member -MemberType NoteProperty -Name 'env' -Value ([PSCustomObject]@{})
    }
    $settings.env | Add-Member -Force -MemberType NoteProperty -Name 'ANTHROPIC_BASE_URL'             -Value "http://127.0.0.1:8082"
    $settings.env | Add-Member -Force -MemberType NoteProperty -Name 'ANTHROPIC_AUTH_TOKEN'           -Value "freecc"
    $settings.env | Add-Member -Force -MemberType NoteProperty -Name 'ANTHROPIC_MODEL'                -Value ""
    $settings.env | Add-Member -Force -MemberType NoteProperty -Name 'ANTHROPIC_DEFAULT_HAIKU_MODEL'  -Value ""
    $settings.env | Add-Member -Force -MemberType NoteProperty -Name 'ANTHROPIC_DEFAULT_SONNET_MODEL' -Value ""
    $settings.env | Add-Member -Force -MemberType NoteProperty -Name 'ANTHROPIC_DEFAULT_OPUS_MODEL'   -Value ""
    $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8
    Write-Host "Settings switched to NIM mode" -ForegroundColor Cyan

    # 4. Launch Claude Code (blocks until exit)
    Set-Location $ProjectDir
    Write-Host "Starting Claude Code (NIM mode)..." -ForegroundColor Green
    fcc-claude

} finally {
    # Runs on normal exit, Ctrl+C, or script error
    Restore-Mimo
}
