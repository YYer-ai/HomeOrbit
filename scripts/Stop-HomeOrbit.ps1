#requires -Version 7.0

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $projectRoot "docker-compose.yml"
$statePath = Join-Path $projectRoot "tmp/runtime/homeorbit-state.json"
$expectedServices = @{
    "homeorbit-postgis" = "postgis"
    "homeorbit-valhalla" = "valhalla"
}

function Assert-ContainerOwnership([string]$ContainerName, [string]$ServiceName) {
    $inspect = docker inspect $ContainerName 2>$null | ConvertFrom-Json
    if (-not $inspect) { throw "找不到容器 $ContainerName。" }
    $labels = $inspect[0].Config.Labels
    if ($labels.'com.docker.compose.project' -ne "homeorbit" -or
        $labels.'com.docker.compose.service' -ne $ServiceName) {
        throw "容器 $ContainerName 不属于 HomeOrbit/$ServiceName，拒绝停止。"
    }
}

function Get-ProcessTree([int]$RootId) {
    $all = @(Get-CimInstance Win32_Process)
    $ids = [System.Collections.Generic.List[int]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($RootId)
    while ($queue.Count -gt 0) {
        $parentId = $queue.Dequeue()
        foreach ($child in $all | Where-Object ParentProcessId -eq $parentId) {
            $ids.Add([int]$child.ProcessId)
            $queue.Enqueue([int]$child.ProcessId)
        }
    }
    return @($ids)
}

if (-not (Test-Path -LiteralPath $statePath)) {
    throw "未找到本轮运行状态文件 $statePath，拒绝推测进程或容器归属。"
}

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
if ($state.schemaVersion -ne 1 -or $state.projectRoot -ne $projectRoot.Replace('\', '/')) {
    throw "状态文件与当前项目不匹配，拒绝停止。"
}

foreach ($record in @($state.processesStartedByScript)) {
    $root = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.Id)" -ErrorAction SilentlyContinue
    if (-not $root) { continue }
    $commandLine = ($root.CommandLine ?? "").Replace('\', '/')
    $marker = ([string]$record.Marker).Replace('\', '/')
    if (-not $commandLine.Contains($marker, [StringComparison]::OrdinalIgnoreCase)) {
        throw "PID $($record.Id) 的命令行不含项目标记 $marker，可能已被复用，拒绝停止。"
    }
    [array]$descendants = Get-ProcessTree ([int]$record.Id)
    [array]::Reverse($descendants)
    foreach ($processId in $descendants) {
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $record.Id -ErrorAction SilentlyContinue
}

foreach ($containerName in @($state.containersStartedByScript)) {
    $serviceName = $expectedServices[$containerName]
    if (-not $serviceName) {
        throw "状态文件包含未知容器 $containerName，拒绝继续。"
    }
    Assert-ContainerOwnership $containerName $serviceName
    docker compose -f $composeFile stop $serviceName
    if ($LASTEXITCODE -ne 0) { throw "停止 $containerName 失败。" }
}

$portsToCheck = @(
    foreach ($record in @($state.processesStartedByScript)) {
        if ($record.Name -eq "web") { 3000 }
        if ($record.Name -eq "api") { 8000 }
    }
    foreach ($containerName in @($state.containersStartedByScript)) {
        if ($containerName -eq "homeorbit-postgis") { 5432 }
        if ($containerName -eq "homeorbit-valhalla") { 8002 }
    }
)
$deadline = [DateTime]::UtcNow.AddSeconds(30)
do {
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalPort -In $portsToCheck)
    if ($listeners.Count -eq 0) { break }
    if ([DateTime]::UtcNow -ge $deadline) {
        throw "关闭后端口仍未释放：$($listeners.LocalPort -join ', ')。已保留状态文件，请检查进程归属。"
    }
    Start-Sleep -Seconds 1
} while ($true)

$resolvedState = (Resolve-Path -LiteralPath $statePath).Path
$allowedRuntimeRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "tmp/runtime"))
if (-not $resolvedState.StartsWith($allowedRuntimeRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "状态文件不在项目 runtime 目录内，拒绝删除。"
}
Remove-Item -LiteralPath $resolvedState -Force

[pscustomobject]@{
    Status = "stopped"
    StoppedProcesses = @($state.processesStartedByScript | ForEach-Object { $_.Id })
    StoppedContainers = @($state.containersStartedByScript)
}
