# 用 Edge 无头模式截图叠加层（验证视觉 / 存档效果图）
# 本机执行策略禁止直接跑 .ps1，用这条命令：
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\shot.ps1 -Url "..." -Out "..\previews\x.png"
# 注意：叠加层带过渡动画，截图务必带 &instant=1，否则截到半透明帧。
param(
    [string]$Url = "http://127.0.0.1:8765/index.html?bg=studio&instant=1",
    [string]$Out = "shot.png",
    [int]$W = 1920,
    [int]$H = 1080,
    [int]$WaitMs = 2500
)

$ErrorActionPreference = 'Stop'
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { throw "找不到 Edge：$edge" }

# 叠加层用 SSE 推真实频谱，长连接会让无头截图等不到"网络空闲"而挂住，
# 所以截图一律带 nosse=1（频谱退回模拟动效，出图效果一样）。
if ($Url -notmatch 'nosse') {
    # 注意别写成 "$Url?nosse=1"：PowerShell 会把它当成变量 $Url?nosse
    if ($Url -match '\?') { $Url = $Url + '&nosse=1' } else { $Url = $Url + '?nosse=1' }
}

if (-not [System.IO.Path]::IsPathRooted($Out)) { $Out = Join-Path (Get-Location) $Out }
$Out = [System.IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null
Remove-Item $Out -ErrorAction SilentlyContinue

$prof = Join-Path $env:TEMP ("edge-shot-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$log = Join-Path $env:TEMP ("edge-shot-log-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".txt")
$log2 = Join-Path $env:TEMP ("edge-shot-log2-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".txt")

$a = @(
    '--headless=new', '--disable-gpu', '--hide-scrollbars',
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
    '--default-background-color=00000000',
    "--user-data-dir=$prof",
    "--window-size=$W,$H",
    "--virtual-time-budget=$WaitMs",
    "--screenshot=$Out",
    $Url
)
# 用 Start-Process 而不是 &，避免 msedge 的 stderr 在 $ErrorActionPreference=Stop 下中断脚本
$p = Start-Process -FilePath $edge -ArgumentList $a -Wait -PassThru `
    -WindowStyle Hidden -RedirectStandardError $log -RedirectStandardOutput $log2
Start-Sleep -Milliseconds 250
Remove-Item $prof -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $log, $log2 -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $Out)) { throw "截图失败：$Url（exit $($p.ExitCode)）" }
$len = (Get-Item $Out).Length
if ($len -lt 20000) { Write-Warning "图很小（$len 字节），可能截到空帧：$Url" }
Write-Output ("ok  {0}  {1} bytes" -f $Out, $len)
