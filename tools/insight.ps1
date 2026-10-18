<#
  一条命令跑完资源分析。

  用法：
    .\insight.ps1             只做静态分析（不需要板子）
    .\insight.ps1 -Runtime    额外采一次串口数据，生成 HTML 报告并自动打开

  第一次用只改下面「需要你改的地方」那四行。
#>

param(
    [switch]$Runtime,          # 加这个参数才会连板子采数据
    [int]$Frames = 30          # 采多少帧
)

$ErrorActionPreference = "Stop"

# ============================================================
#  需要你改的地方（只有这里需要动）
# ============================================================
$ProjectDir = "D:\ESP32-FAE\ESP32-Light-Sensor-Monitor-github"  # 你的 ESP-IDF 工程目录
$Partition  = "1500K"                                          # 分区表里 app 分区大小，留空则跳过
$ComPort    = "COM19"                                          # 板子的串口号，只有 -Runtime 才用
$Config     = "budgets\light.json"                             # 阈值配置，只有 -Runtime 才用
# ============================================================

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python   = Join-Path $RepoRoot ".venv\Scripts\python.exe"

function Say($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host ""; Write-Host "错误: $msg" -ForegroundColor Red; exit 1 }

# ---------- 前置检查 ----------
if (-not (Test-Path -LiteralPath $ProjectDir)) { Fail "工程目录不存在: $ProjectDir" }
if (-not (Test-Path -LiteralPath $Python))     { Fail "找不到 Python 虚拟环境，先在本仓库根目录执行 python -m venv .venv 并 pip install -e ." }
if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    Fail "找不到 idf.py。请先打开「ESP-IDF PowerShell」窗口，再运行本脚本。"
}

# ---------- 1. 编译 ----------
Say "编译工程（idf.py build）"
Push-Location $ProjectDir
try {
    idf.py build
    if ($LASTEXITCODE -ne 0) { Fail "编译失败，先修好编译错误" }
} finally {
    Pop-Location
}

# ---------- 2. 找构建产物 ----------
$mapFile = Get-ChildItem -LiteralPath (Join-Path $ProjectDir "build") -Filter *.map -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $mapFile) { Fail "build 目录里没有 .map 文件" }
$binFile = [System.IO.Path]::ChangeExtension($mapFile.FullName, ".bin")
$elfFile = [System.IO.Path]::ChangeExtension($mapFile.FullName, ".elf")
if (-not (Test-Path -LiteralPath $binFile)) { Fail "找不到对应的 .bin: $binFile" }

Write-Host "    工程   : $($mapFile.BaseName)"
Write-Host "    Flash  : $binFile"

# ---------- 3. 静态分析 ----------
Say "静态分析（Flash / RAM 归因）"
$outDir = Join-Path $ProjectDir "build\insight"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$analyzeOut = Join-Path $outDir "analyze.txt"

$analyzeArgs = @("-m", "mcu_insight", "analyze", $mapFile.FullName, "--bin", $binFile)
if ($Partition) { $analyzeArgs += @("--partition", $Partition) }

Push-Location $RepoRoot
try {
    & $Python @analyzeArgs | Tee-Object -FilePath $analyzeOut
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "静态结果已保存: $analyzeOut" -ForegroundColor Green

# ---------- 4. 运行期（可选） ----------
if (-not $Runtime) {
    Write-Host ""
    Write-Host "完成。想连板子看栈/堆，加 -Runtime 再跑一次。" -ForegroundColor Green
    exit 0
}

Say "串口采集（$ComPort，$Frames 帧）"
$dbPath = Join-Path $RepoRoot ("captures\insight-" + $mapFile.BaseName + ".db")
Push-Location $RepoRoot
try {
    & $Python -m mcu_insight collect --db $dbPath --source "serial:$ComPort" --limit $Frames
    if ($LASTEXITCODE -ne 0) { Fail "采集失败：检查串口号是否被占用（别同时开 idf.py monitor）" }

    Say "阈值判定"
    $configPath = Join-Path $RepoRoot $Config
    & $Python -m mcu_insight check --db $dbPath --config $configPath

    Say "生成 HTML 报告"
    $html = Join-Path $outDir "report.html"
    $reportArgs = @("-m", "mcu_insight", "report", "--db", $dbPath, "--config", $configPath,
                    "--map", $mapFile.FullName, "--bin", $binFile, "--out", $html)
    if ($Partition) { $reportArgs += @("--partition", $Partition) }
    & $Python @reportArgs
    if ($LASTEXITCODE -ne 0) { Fail "报告生成失败" }
} finally {
    Pop-Location
}

if (Test-Path -LiteralPath $html) {
    Write-Host ""
    Write-Host "报告: $html" -ForegroundColor Green
    Start-Process $html
}