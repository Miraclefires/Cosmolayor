$ErrorActionPreference = 'Stop'
try {
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

    $mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])

    $sessions = $mgr.GetSessions()
    "会话数量: $($sessions.Count)"

    foreach ($s in $sessions) {
        "------------"
        "来源App : $($s.SourceAppUserModelId)"
        try {
            $props = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
            if ($props) {
                "标题    : $($props.Title)"
                "作者    : $($props.Artist)"
                "专辑    : $($props.AlbumTitle)"
                "封面流  : $(if ($props.Thumbnail) { '有' } else { '无' })"
            }
        } catch { "属性读取失败: $($_.Exception.Message)" }

        try {
            $tl = $s.GetTimelineProperties()
            "进度    : $($tl.Position) / $($tl.EndTime)"
        } catch { "时间轴读取失败: $($_.Exception.Message)" }

        try {
            $info = $s.GetPlaybackInfo()
            "播放状态: $($info.PlaybackStatus)"
        } catch { "状态读取失败" }
    }
}
catch {
    "SMTC 探测失败: $($_.Exception.Message)"
}
