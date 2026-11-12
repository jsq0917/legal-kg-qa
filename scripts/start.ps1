# LexWeave 一键启动：FastAPI + Streamlit 四页签
# 用法：powershell -ExecutionPolicy Bypass -File scripts/start.ps1
#      换端口：scripts/start.ps1 -ApiPort 8010 -UiPort 8601
#      只起界面：scripts/start.ps1 -SkipApi
# 说明 1：直接用 .venv，不执行 uv sync —— 裸 sync 会清掉 pyproject 里没声明的可选依赖。
# 说明 2：Streamlit 界面是进程内直调（import 服务和图），不依赖 API 进程。
# 说明 3：默认 8010/8501。8000 常被 Windows http.sys 或其他项目占用，不再作为默认；
#         端口冲突时自动顺延到备用端口并明确提示，不会去杀别的进程。
# 说明 4：退出码 0=成功，1=环境缺失，2=界面无端口可用，3=API 启动失败，4=依赖探测失败。

param(
    [int]$ApiPort = 8010,
    [int]$UiPort = 8501,
    [switch]$SkipApi
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # 关掉 Invoke-WebRequest 的进度条噪音
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONUTF8 = '1'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root '.venv\Scripts\python.exe'
$Streamlit = Join-Path $Root '.venv\Scripts\streamlit.exe'

function Fail([string]$Message, [int]$Code) {
    Write-Host ""
    Write-Host "[启动失败] $Message" -ForegroundColor Red
    exit $Code
}

function Get-ListeningPid([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { return $conn[0].OwningProcess }
    return $null
}

function Get-PortOwner([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conn) { return $null }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($conn[0].OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $proc) { return "PID $($conn[0].OwningProcess)" }
    $cmd = $proc.CommandLine
    if ($cmd -and $cmd.Length -gt 80) { $cmd = $cmd.Substring(0, 80) + '...' }
    return "$($proc.Name) (PID $($proc.ProcessId)) $cmd"
}

if (-not (Test-Path $Py)) {
    Fail "找不到虚拟环境：$Py`n请先在本目录建好 .venv（uv venv --python 3.13 后按 README 安装依赖）。" 1
}

# ---- 依赖：Neo4j 容器（停机则拉起；拉不起来只告警，不阻塞）----
$Neo4jName = 'lexweave-neo4j'
$neo4jState = $null
if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        $neo4jState = (docker ps -a --filter "name=$Neo4jName" --format "{{.State}}" 2>$null | Select-Object -First 1)
    } catch { $neo4jState = $null }
}
if ($neo4jState -eq 'running') {
    Write-Host "[0/2] Neo4j 容器 $Neo4jName 已在运行。" -ForegroundColor DarkGray
} elseif ($neo4jState) {
    Write-Host "[0/2] Neo4j 容器 $Neo4jName 处于 $neo4jState，正在启动 ..." -ForegroundColor Cyan
    docker start $Neo4jName | Out-Null
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        $h = (docker inspect --format "{{.State.Health.Status}}" $Neo4jName 2>$null)
        if ($h -eq 'healthy') { break }
    }
    if ($h -eq 'healthy') {
        Write-Host "      Neo4j 已 healthy。" -ForegroundColor Green
    } else {
        Write-Host "      Neo4j 90 秒内未 healthy，/health 可能回落 NetworkX。" -ForegroundColor Yellow
    }
} else {
    Write-Host "[0/2] 未找到 Neo4j 容器 $Neo4jName（Docker 未装/未启动），图后端将回落 NetworkX。" -ForegroundColor Yellow
}

# ---- API 进程（可跳过）----
$apiProc = $null
$apiPortUsed = $ApiPort
if ($SkipApi) {
    Write-Host "[1/2] 按参数跳过 API。" -ForegroundColor DarkGray
} else {
    $owner = Get-PortOwner $ApiPort
    if ($owner) {
        Write-Host "[1/2] 端口 $ApiPort 已被占用：$owner" -ForegroundColor Yellow
        $picked = 0
        foreach ($cand in 8011, 8012, 8013) {
            if (-not (Get-PortOwner $cand)) { $picked = $cand; break }
        }
        if ($picked -eq 0) {
            Fail "端口 $ApiPort 及备用端口 8011-8013 都被占用，API 无法启动。请先释放端口或改 -ApiPort。" 3
        }
        $apiPortUsed = $picked
        Write-Host "      改用 $apiPortUsed 启动 API（没有动占用者）。" -ForegroundColor Yellow
    }

    Write-Host "[1/2] 启动 FastAPI: http://127.0.0.1:$apiPortUsed/docs" -ForegroundColor Cyan
    $apiProc = Start-Process -FilePath $Py -ArgumentList '-m', 'uvicorn', 'app.__008__api.__000__server:app', '--host', '127.0.0.1', '--port', "$apiPortUsed" -WorkingDirectory $Root -PassThru

    $ready = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Milliseconds 1000
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$apiPortUsed/health" -TimeoutSec 3 -UseBasicParsing
            if ($resp.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        Fail "API 在 90 秒内没有就绪（端口 $apiPortUsed，PID $($apiProc.Id)）。请到弹出的 uvicorn 窗口看报错；必要时 Stop-Process -Id $($apiProc.Id)。" 3
    }

    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$apiPortUsed/health" -TimeoutSec 5
        Write-Host "      API 已就绪，PID $($apiProc.Id)；图后端 $($h.graph_backend)，条文 $($h.corpus_articles) 条。" -ForegroundColor Green
        if ($h.graph_backend -ne 'neo4j') {
            Write-Host "      警告：图后端不是 neo4j（当前 $($h.graph_backend)），图统计口径会与 README 不一致。" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "      API 已就绪，PID $($apiProc.Id)（/health 读取失败，稍后自行复查）。" -ForegroundColor Yellow
    }
}

# ---- Streamlit 界面 ----
$uiPortUsed = $UiPort
$owner = Get-PortOwner $UiPort
if ($owner) {
    Write-Host "[2/2] 端口 $UiPort 已被占用：$owner" -ForegroundColor Yellow
    $picked = 0
    foreach ($cand in 8502, 8601, 8602) {
        if (-not (Get-PortOwner $cand)) { $picked = $cand; break }
    }
    if ($picked -eq 0) {
        Fail "端口 $UiPort 及备用端口 8502/8601/8602 都被占用，界面无法启动。请先释放端口或改 -UiPort。" 2
    }
    $uiPortUsed = $picked
    Write-Host "      改用 $uiPortUsed 启动界面（没有动占用者）。" -ForegroundColor Yellow
}

Write-Host "[2/2] 启动 Streamlit 四页签: http://127.0.0.1:$uiPortUsed" -ForegroundColor Cyan
$uiProc = Start-Process -FilePath $Streamlit -ArgumentList 'run', 'app\__009__ui\__000__app.py', '--server.port', "$uiPortUsed", '--server.address', '127.0.0.1', '--server.headless', 'true' -WorkingDirectory $Root -PassThru

$uiReady = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 1000
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$uiPortUsed/_stcore/health" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $uiReady = $true; break }
    } catch { }
}
if ($uiReady) {
    Write-Host "      界面已就绪，PID $($uiProc.Id)。" -ForegroundColor Green
} else {
    Write-Host "      界面 60 秒内未就绪（端口 $uiPortUsed，PID $($uiProc.Id)），请到弹出的 Streamlit 窗口看报错。" -ForegroundColor Yellow
}

# ---- 汇总（PID 取真正监听端口的进程；.venv 的 python.exe 是转发壳，Start-Process 拿到的是壳）----
$apiPid = if ($SkipApi) { $null } else { Get-ListeningPid $apiPortUsed }
$uiPid = Get-ListeningPid $uiPortUsed

Write-Host ""
Write-Host "启动完成：" -ForegroundColor Green
if ($apiPid) {
    Write-Host "  API   http://127.0.0.1:$apiPortUsed/health   PID $apiPid"
} else {
    Write-Host "  API   已跳过"
}
Write-Host "  界面  http://127.0.0.1:$uiPortUsed   PID $uiPid"
Write-Host "  日志  在各自弹出的控制台窗口里滚动；关闭该窗口即停止对应服务"
$stopIds = @(@($apiPid) + @($uiPid) | Where-Object { $_ }) -join ','
Write-Host "  停止   Stop-Process -Id $stopIds"
exit 0
