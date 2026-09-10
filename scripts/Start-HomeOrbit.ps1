#requires -Version 7.0

[CmdletBinding()]
param(
    [switch]$ServicesOnly,
    [int]$HealthTimeoutSeconds = 120
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$composeFile = Join-Path $projectRoot "docker-compose.yml"
$runtimeRoot = Join-Path $projectRoot "tmp/runtime"
$statePath = Join-Path $runtimeRoot "homeorbit-state.json"
$logRoot = Join-Path $runtimeRoot "logs"
$tempRoot = Join-Path $runtimeRoot "temp"
$expectedContainers = @{
    "homeorbit-postgis" = "postgis"
    "homeorbit-valhalla" = "valhalla"
}

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "缺少命令：$Name"
    }
}

function Wait-DockerDesktop([int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        docker info --format '{{.ServerVersion}}' 1>$null 2>$null
        if ($LASTEXITCODE -eq 0) { return }

        $rawStatus = docker desktop status --format json 2>$null
        if ($LASTEXITCODE -eq 0 -and $rawStatus) {
            $desktopStatus = $rawStatus | ConvertFrom-Json
            if ($desktopStatus.Status -eq "running") { return }
            if ($desktopStatus.Status -notin @("starting", "stopping")) {
                throw "Docker Desktop 状态为 $($desktopStatus.Status)，请先人工启动后重试。"
            }
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "等待 Docker Desktop 就绪超时（${TimeoutSeconds}s）。"
}

function Assert-ContainerOwnership([string]$ContainerName, [string]$ServiceName) {
    $inspect = docker inspect $ContainerName 2>$null | ConvertFrom-Json
    if (-not $inspect) {
        return
    }
    $labels = $inspect[0].Config.Labels
    if ($labels.'com.docker.compose.project' -ne "homeorbit" -or
        $labels.'com.docker.compose.service' -ne $ServiceName) {
        throw "容器 $ContainerName 不属于 HomeOrbit/$ServiceName，拒绝操作。"
    }
}

function Get-ContainerStatus([string]$ContainerName) {
    $status = docker inspect $ContainerName --format '{{.State.Status}}' 2>$null
    if ($LASTEXITCODE -ne 0) { return "missing" }
    return $status.Trim()
}

function Wait-Healthy([string]$ContainerName, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $health = docker inspect $ContainerName --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>$null
        if ($LASTEXITCODE -eq 0 -and $health.Trim() -eq "healthy") { return }
        if ($health.Trim() -eq "unhealthy") {
            throw "$ContainerName 健康检查失败。"
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "等待 $ContainerName 健康状态超时（${TimeoutSeconds}s）。"
}

function Assert-PortsFree([int[]]$Ports) {
    $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object LocalPort -In $Ports
    if ($listeners) {
        $details = $listeners | ForEach-Object { "端口 $($_.LocalPort)，PID $($_.OwningProcess)" }
        throw "检测到现有监听，拒绝覆盖：$($details -join '；')"
    }
}

function Wait-HttpReady([string]$Url, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return }
        }
        catch { }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "等待 $Url 就绪超时，请查看 $logRoot。"
}

function Stop-StartedContainers([string[]]$ContainerNames) {
    foreach ($containerName in $ContainerNames) {
        $serviceName = $expectedContainers[$containerName]
        Assert-ContainerOwnership $containerName $serviceName
        docker compose -f $composeFile stop $serviceName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "停止 $containerName 失败。" }
    }
}

New-Item -ItemType Directory -Force -Path $runtimeRoot, $logRoot, $tempRoot | Out-Null
Assert-Command "docker"
Wait-DockerDesktop $HealthTimeoutSeconds

if (Test-Path -LiteralPath $statePath) {
    throw "检测到运行状态文件 $statePath。请先执行 scripts/Stop-HomeOrbit.ps1，或人工核对后处理。"
}

$ports = if ($ServicesOnly) { @(5432, 8002) } else { @(3000, 5432, 8000, 8002) }
Assert-PortsFree $ports

$startedContainers = [System.Collections.Generic.List[string]]::new()
$startedProcesses = [System.Collections.Generic.List[object]]::new()

try {
    foreach ($entry in $expectedContainers.GetEnumerator()) {
        Assert-ContainerOwnership $entry.Key $entry.Value
        if ((Get-ContainerStatus $entry.Key) -ne "running") {
            $startedContainers.Add($entry.Key)
        }
    }

    docker compose -f $composeFile up -d postgis valhalla
    if ($LASTEXITCODE -ne 0) { throw "启动 HomeOrbit Docker 服务失败。" }
    Wait-Healthy "homeorbit-postgis" $HealthTimeoutSeconds
    Wait-Healthy "homeorbit-valhalla" $HealthTimeoutSeconds

    if (-not $ServicesOnly) {
        Assert-Command "uv"
        Assert-Command "npm.cmd"

        $managedEnvironment = @{
            TEMP = $tempRoot
            TMP = $tempRoot
            UV_CACHE_DIR = (Join-Path $projectRoot ".uv-cache")
            npm_config_cache = (Join-Path $projectRoot ".npm-cache")
            PYTHONDONTWRITEBYTECODE = "1"
            HOMEORBIT_DATABASE_URL = "postgresql://homeorbit:homeorbit_dev@127.0.0.1:5432/homeorbit"
            HOMEORBIT_VALHALLA_URL = "http://127.0.0.1:8002"
        }
        $previousEnvironment = @{}
        foreach ($name in $managedEnvironment.Keys) {
            $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
            [Environment]::SetEnvironmentVariable($name, $managedEnvironment[$name], "Process")
        }

        try {
            $apiProcess = Start-Process -FilePath (Get-Command "uv").Source `
                -ArgumentList @("run", "--directory", (Join-Path $projectRoot "api"), "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
                -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $logRoot "api.stdout.log") `
                -RedirectStandardError (Join-Path $logRoot "api.stderr.log")
            $startedProcesses.Add([pscustomobject]@{ Name = "api"; Id = $apiProcess.Id; Marker = (Join-Path $projectRoot "api") })

            $webProcess = Start-Process -FilePath (Get-Command "npm.cmd").Source `
                -ArgumentList @("--prefix", (Join-Path $projectRoot "web"), "run", "dev", "--", "--hostname", "127.0.0.1", "--port", "3000") `
                -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $logRoot "web.stdout.log") `
                -RedirectStandardError (Join-Path $logRoot "web.stderr.log")
            $startedProcesses.Add([pscustomobject]@{ Name = "web"; Id = $webProcess.Id; Marker = (Join-Path $projectRoot "web") })
        }
        finally {
            foreach ($name in $managedEnvironment.Keys) {
                [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
            }
        }
    }

    $state = [ordered]@{
        schemaVersion = 1
        projectRoot = $projectRoot.Replace('\', '/')
        startedAtUtc = [DateTime]::UtcNow.ToString("o")
        servicesOnly = [bool]$ServicesOnly
        containersStartedByScript = @($startedContainers)
        processesStartedByScript = @($startedProcesses)
        tempRoot = $tempRoot.Replace('\', '/')
        logRoot = $logRoot.Replace('\', '/')
    }
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

    if (-not $ServicesOnly) {
        Wait-HttpReady "http://127.0.0.1:8000/gis/health" $HealthTimeoutSeconds
        Wait-HttpReady "http://127.0.0.1:3000" $HealthTimeoutSeconds
        Write-Host "HomeOrbit 已就绪：http://127.0.0.1:3000"
    }

    [pscustomobject]@{
        Status = "started"
        ServicesOnly = [bool]$ServicesOnly
        StatePath = $statePath.Replace('\', '/')
        ProcessIds = @($startedProcesses | ForEach-Object { $_.Id })
    }
}
catch {
    if (Test-Path -LiteralPath $statePath) {
        & (Join-Path $PSScriptRoot "Stop-HomeOrbit.ps1")
    }
    else {
        foreach ($process in $startedProcesses) {
            Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
        }
        if ($startedContainers.Count -gt 0) {
            Stop-StartedContainers @($startedContainers)
        }
    }
    throw
}
