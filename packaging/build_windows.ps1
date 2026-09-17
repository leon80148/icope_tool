# 在 Windows 建置 IcopeTool（PyInstaller onedir）並壓成 zip。
# 用法：powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "venv\Scripts\python.exe")) {
    py -3.11 -m venv venv
}
$python = Join-Path $root "venv\Scripts\python.exe"

& $python -m pip install --disable-pip-version-check -q -r requirements-dev.txt
# ddddocr 的套件宣告在 Windows 會拉進非 headless 的 opencv-python，因此不裝它的依賴
& $python -m pip install --disable-pip-version-check -q --no-deps ddddocr==1.6.1
& $python -m pip install --disable-pip-version-check -q --no-deps -e .

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
Compress-Archive -Path dist\IcopeTool -DestinationPath $zip
Write-Host "完成：$zip"
