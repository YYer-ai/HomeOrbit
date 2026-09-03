#requires -Version 7.0

[CmdletBinding()]
param(
    [ValidateRange(2, 20)]
    [int]$Cycles = 5,
    [int]$SettleSeconds = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$startScript = Join-Path $PSScriptRoot "Start-HomeOrbit.ps1"
$stopScript = Join-Path $PSScriptRoot "Stop-HomeOrbit.ps1"
$statePath = Join-Path $projectRoot "tmp/runtime/homeorbit-state.json"
$vhdxPath = "C:/Users/$env:USERNAME/AppData/Local/Docker/wsl/disk/docker_data.vhdx"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$resultRoot = Join-Path $projectRoot "tmp/storage-measurement/$timestamp"
$csvPath = Join-Path $resultRoot "samples.csv"
$summaryPath = Join-Path $resultRoot "summary.json"
$samples = [System.Collections.Generic.List[object]]::new()

function Get-Sample([int]$Cycle, [string]$Phase) {
    $vhdx = Get-Item -LiteralPath $vhdxPath -ErrorAction SilentlyContinue
    $containers = docker inspect --size "homeorbit-postgis" "homeorbit-valhalla" 2>$null | ConvertFrom-Json
    $rwBytes = if ($containers) { ($containers | Measure-Object -Property SizeRw -Sum).Sum } else { 0 }
    $dockerTotals = docker system df --format '{{json .}}' | ForEach-Object { $_ | ConvertFrom-Json }
    $imageTotal = $dockerTotals | Where-Object Type -eq "Images"
    $volumeTotal = $dockerTotals | Where-Object Type -eq "Local Volumes"
    return [pscustomobject]@{
        TimestampUtc = [DateTime]::UtcNow.ToString("o")
        Cycle = $Cycle
        Phase = $Phase
        VhdxBytes = if ($vhdx) { $vhdx.Length } else { $null }
        ContainerWritableBytes = [int64]$rwBytes
        DockerImagesTotal = $imageTotal.Size
        DockerVolumesTotal = $volumeTotal.Size
    }
}

if (Test-Path -LiteralPath $statePath) {
    throw "HomeOrbit 状态文件已存在。请先正常停止当前运行实例，避免测量脚本干扰现有服务。"
}

$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object LocalPort -In @(5432, 8002)
if ($listeners) {
    throw "5432 或 8002 已有监听，拒绝执行启停测量。"
}

New-Item -ItemType Directory -Force -Path $resultRoot | Out-Null
$samples.Add((Get-Sample 0 "before"))

try {
    for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
        & $startScript -ServicesOnly | Out-Null
        Start-Sleep -Seconds $SettleSeconds
        $samples.Add((Get-Sample $cycle "running"))

        & $stopScript | Out-Null
        Start-Sleep -Seconds $SettleSeconds
        $samples.Add((Get-Sample $cycle "stopped"))
    }
}
finally {
    if (Test-Path -LiteralPath $statePath) {
        & $stopScript | Out-Null
    }
}

$samples | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding utf8
$first = $samples[0]
$last = $samples[$samples.Count - 1]
$summary = [ordered]@{
    measuredAtUtc = [DateTime]::UtcNow.ToString("o")
    cycles = $Cycles
    settleSeconds = $SettleSeconds
    initialVhdxBytes = $first.VhdxBytes
    finalVhdxBytes = $last.VhdxBytes
    vhdxDeltaBytes = if ($null -ne $first.VhdxBytes -and $null -ne $last.VhdxBytes) {
        [int64]$last.VhdxBytes - [int64]$first.VhdxBytes
    } else { $null }
    maxContainerWritableBytes = ($samples | Measure-Object -Property ContainerWritableBytes -Maximum).Maximum
    samplesCsv = $csvPath.Replace('\', '/')
}
$summary | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $summaryPath -Encoding utf8
$summary
