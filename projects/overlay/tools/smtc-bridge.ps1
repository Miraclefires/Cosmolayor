# SMTC 数据源（常驻循环，每次输出一行 JSON 给 bridge.py）
# 只在 Windows PowerShell 5.1 下可用（WinRT 投影），必须用 powershell.exe 跑：
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\smtc-bridge.ps1 -IntervalMs 800 -CoverPath runtime\cover.jpg
# 单独跑时每行 JSON 直接看得到，相当于实时探针。
param(
    [int]$IntervalMs = 800,
    [string]$CoverPath = '',
    [string]$Sources = 'cloudmusic,spotify,qqmusic,foobar,musicbee,aimp,potplayer,zune'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
[Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime] | Out-Null

try {
    $mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
} catch {
    [Console]::Out.WriteLine((@{ ok = $false; fatal = "SMTC 初始化失败：$($_.Exception.Message)" } | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
    exit 1
}

$allow = $Sources.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ }
$lastKey = ''
$coverPathFull = if ($CoverPath) { [System.IO.Path]::GetFullPath($CoverPath) } else { '' }
if ($coverPathFull) {
    $dir = Split-Path $coverPathFull
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
}

while ($true) {
    $out = @{ ok = $false; ts = [DateTimeOffset]::Now.ToUnixTimeMilliseconds() }

    try {
        $pick = $null
        foreach ($s in $mgr.GetSessions()) {
            $id = "$($s.SourceAppUserModelId)".ToLowerInvariant()
            foreach ($a in $allow) {
                if ($id.Contains($a)) { $pick = $s; break }
            }
            if ($pick) { break }
        }

        if (-not $pick) {
            $out.ok = $true
            $out.none = $true
        }
        else {
            $props = $null
            try { $props = Await ($pick.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]) } catch { }

            $title = ''; $artist = ''; $album = ''
            if ($props) {
                $title = "$($props.Title)"
                $artist = "$($props.Artist)"
                $album = "$($props.AlbumTitle)"
            }

            $status = 'Closed'
            try { $status = "$($pick.GetPlaybackInfo().PlaybackStatus)" } catch { }
            $pos = 0.0; $end = 0.0
            try {
                $tl = $pick.GetTimelineProperties()
                $pos = [double]$tl.Position.TotalSeconds
                $end = [double]$tl.EndTime.TotalSeconds
            } catch { }

            $out.ok = $true
            $out.source = "$($pick.SourceAppUserModelId)"
            $out.title = $title
            $out.artist = $artist
            $out.album = $album
            $out.playing = ($status -eq 'Playing')
            $out.status = $status
            $out.position = [Math]::Round($pos, 2)
            $out.duration = [Math]::Round($end, 2)

            # 封面：只在换歌时抓一次，落盘给浏览器读
            $key = "$title|$artist"
            if ($coverPathFull -and $key -ne $lastKey) {
                $lastKey = $key
                $tmp = "$coverPathFull.tmp"
                $got = $false
                if ($props -and $props.Thumbnail) {
                    try {
                        $stream = Await ($props.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
                        $size = [uint32]$stream.Size
                        if ($size -gt 0) {
                            $reader = [Windows.Storage.Streams.DataReader]::new($stream)
                            Await ($reader.LoadAsync($size)) ([uint32]) | Out-Null
                            $bytes = New-Object byte[] $size
                            $reader.ReadBytes($bytes)
                            [System.IO.File]::WriteAllBytes($tmp, $bytes)
                            Move-Item -LiteralPath $tmp -Destination $coverPathFull -Force
                            $got = $true
                            $reader.Dispose()
                        }
                        $stream.Dispose()
                    } catch { }
                }
                if (-not $got) {
                    # 新歌没有封面：清掉旧图，别显示上一首的
                    Remove-Item -LiteralPath $coverPathFull -Force -ErrorAction SilentlyContinue
                }
            }
            $out.cover = ($coverPathFull -and (Test-Path $coverPathFull))
        }
    }
    catch {
        $out.error = $_.Exception.Message
    }

    [Console]::Out.WriteLine(($out | ConvertTo-Json -Compress -Depth 4))
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds $IntervalMs
}
