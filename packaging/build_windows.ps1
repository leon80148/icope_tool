# 在 Windows 建置 IcopeTool（PyInstaller onedir）並壓成 zip。
# 用法：powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "venv\Scripts\python.exe")) {
    py -3.11 -m venv venv
}
$python = Join-Path $root "venv\Scripts\python.exe"

# 原生指令失敗不會觸發 $ErrorActionPreference：每一步自己檢查，否則會一路跑到 pytest 才以「測試失敗」收場
& $python -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "安裝相依套件失敗（requirements-dev.txt）" }
# ddddocr 的套件宣告在 Windows 會拉進非 headless 的 opencv-python，因此不裝它的依賴
& $python -m pip install --disable-pip-version-check -q --no-deps ddddocr==1.6.1
if ($LASTEXITCODE -ne 0) { throw "安裝 ddddocr 失敗" }
& $python -m pip install --disable-pip-version-check -q --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw "安裝本專案失敗（pip install -e .）" }

if (-not (Test-Path "icope_tool\resources\app.ico")) {
    & $python tools\make_icon.py
}

& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "測試失敗，停止打包" }

& $python -m PyInstaller --noconfirm --clean packaging\IcopeTool.spec --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失敗" }

Copy-Item docs\INSTALL.md dist\IcopeTool\安裝與使用說明.md -Force
Copy-Item CHANGELOG.md dist\IcopeTool\更新紀錄.md -Force
Copy-Item LICENSE dist\IcopeTool\LICENSE.txt -Force
$version = (& $python -c "import icope_tool; print(icope_tool.__version__)").Trim()
$zip = "dist\IcopeTool-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
# 不用 Compress-Archive：Windows PowerShell 5.1 的版本會把 zip 裡的路徑寫成反斜線（不符合 zip 規格），
# 檔案總管解得開，但部分第三方解壓工具會解成一堆檔名含反斜線的檔案
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory((Join-Path $root "dist\IcopeTool"), (Join-Path $root $zip),
    [System.IO.Compression.CompressionLevel]::Optimal, $true)
Write-Host "完成：$zip"
