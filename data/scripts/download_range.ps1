param(
    [Parameter(Mandatory)] [string] $Url,
    [Parameter(Mandatory)] [string] $OutputPath,
    [Parameter(Mandatory)] [long] $TotalBytes,
    [long] $ChunkBytes = 8388608,
    [long] $RemoteOffset = 0,
    [string] $Proxy,
    [string] $Resolve
)

$ErrorActionPreference = "Stop"
$curl = (Get-Command curl.exe).Source
$chunkPath = "$OutputPath.chunk"
$reportEvery = 67108864L
$start = if (Test-Path -LiteralPath $OutputPath) {
    (Get-Item -LiteralPath $OutputPath).Length
} else {
    0L
}
$lastReport = $start

if ($start -gt $TotalBytes) {
    throw "Existing file is larger than expected: $start > $TotalBytes"
}

while ($start -lt $TotalBytes) {
    $end = [Math]::Min($start + $ChunkBytes - 1, $TotalBytes - 1)
    $expected = $end - $start + 1
    $remoteStart = $RemoteOffset + $start
    $remoteEnd = $RemoteOffset + $end
    $reuseChunk = (Test-Path -LiteralPath $chunkPath) -and
        ((Get-Item -LiteralPath $chunkPath).Length -eq $expected)

    if (-not $reuseChunk) {
        $arguments = @(
            "-L", "--fail", "--silent", "--show-error",
            "--retry", "5", "--retry-all-errors", "--retry-delay", "3",
            "--max-time", "120", "--range", "$remoteStart-$remoteEnd",
            "--output", $chunkPath
        )
        if ($Proxy) {
            $arguments += @("--proxy", $Proxy)
        }
        if ($Resolve) {
            $arguments += @("--noproxy", "*", "--resolve", $Resolve)
        }
        $arguments += $Url
        & $curl @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "curl failed with exit code $LASTEXITCODE for bytes $remoteStart-$remoteEnd"
        }
    }

    $actual = (Get-Item -LiteralPath $chunkPath).Length
    if ($actual -ne $expected) {
        throw "Chunk size mismatch for bytes $remoteStart-$remoteEnd`: $actual != $expected"
    }

    $output = [System.IO.File]::Open(
        $OutputPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    try {
        if ($output.Length -ne $start) {
            throw "Output size changed unexpectedly: $($output.Length) != $start"
        }
        $output.Position = $start
        $chunk = [System.IO.File]::OpenRead($chunkPath)
        try {
            $chunk.CopyTo($output)
        } finally {
            $chunk.Dispose()
        }
        $output.Flush($true)
    } finally {
        $output.Dispose()
    }

    Remove-Item -LiteralPath $chunkPath -Force
    $start = $end + 1
    if (($start - $lastReport -ge $reportEvery) -or ($start -eq $TotalBytes)) {
        Write-Output "$start/$TotalBytes"
        $lastReport = $start
    }
}
