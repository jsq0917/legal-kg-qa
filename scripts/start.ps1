# LexWeave 一键启动：FastAPI + Streamlit 四页签
# 用法：powershell -ExecutionPolicy Bypass -File scripts/start.ps1
#      换端口：scripts/start.ps1 -ApiPort 8010 -UiPort 8601
#      只起界面：scripts/start.ps1 -SkipApi
# 说明 1：直接用 .venv，不执行 uv sync —— 裸 sync 会清掉 pyproject 里没声明的可选依赖。
# 说明 2：Streamlit 界面是进程内直调（import 服务和图），不依赖 API 进程；
#         所以 API 端口被占（例如 lexflow 也用 8000）时，界面照常可用。

param(
    [int]$ApiPort = 8000,
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

if (-not (Test-Path $Py)) {
    Write-Host "找不到虚拟环境：$Py" -ForegroundColor Red
    Write-Host "请先在本目录建好 .venv（uv venv --python 3.13 后按 README 安装依赖）。" -ForegroundColor Yellow
    exit 1
}

function Get-PortOwner([int]$Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conn) { return $null }
    $p = Get-Process -Id $conn[0].OwningProcess -ErrorAction SilentlyContinue
    if ($p) { return "$($p.ProcessName) (PID $($p.Id))" }
    return "PID $($conn[0].OwningProcess)"
}

# ---- API 进程（可跳过）----
if ($SkipApi) {
    Write-Host "[1/2] 按参数跳过 API。" -ForegroundColor DarkGray
} else {
    $owner = Get-PortOwner $ApiPort
    if ($owner) {
        Write-Host "[1/2] 端口 $ApiPort 被 $owner 占用，跳过 API。" -ForegroundColor Yellow
        Write-Host "      界面是进程内直调，不依赖 API，仍会正常启动；要单独起 API 用 -ApiPort 换端口。" -ForegroundColor DarkGray
    } else {
        Write-Host "[1/2] 启动 FastAPI: http://127.0.0.1:$ApiPort/docs" -ForegroundColor Cyan
        # 直接用 uvicorn 起，才能让 -ApiPort 生效（模块的 __main__ 里写死了 8000）
        Start-Process -FilePath $Py -ArgumentList '-m', 'uvicorn', 'app.__008__api.__000__server:app', '--host', '127.0.0.1', '--port', "$ApiPort" -WorkingDirectory $Root

        $ready = $false
        for ($i = 0; $i -lt 90; $i++) {
            Start-Sleep -Milliseconds 1000
            try {
                $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/docs" -TimeoutSec 3 -UseBasicParsing
                if ($resp.StatusCode -eq 200) { $ready = $true; break }
            } catch { }
        }
        if ($ready) {
            Write-Host "      API 已就绪。" -ForegroundColor Green
        } else {
            Write-Host "      90 秒内未就绪，请看新窗口报错。" -ForegroundColor Yellow
        }
    }
}

# ---- Streamlit 界面 ----
$uiOwner = Get-PortOwner $UiPort
if ($uiOwner) {
    Write-Host "[2/2] 端口 $UiPort 被 $uiOwner 占用。" -ForegroundColor Yellow
    Write-Host "      换一个：scripts\start.ps1 -UiPort 8601" -ForegroundColor Yellow
    exit 1
}

Write-Host "[2/2] 启动 Streamlit 四页签: http://127.0.0.1:$UiPort" -ForegroundColor Cyan
Start-Process -FilePath $Streamlit -ArgumentList 'run', 'app\__009__ui\__000__app.py', '--server.port', "$UiPort" -WorkingDirectory $Root

Write-Host "已在新窗口启动；关闭对应窗口即可停止服务。" -ForegroundColor Green
