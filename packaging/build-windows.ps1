# Gera dist\installer\RoryAssistant-Setup-<versao>.exe
# Uso (da raiz do projeto):  powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    py -m venv .venv
    if ($LASTEXITCODE) { throw 'venv falhou' }
    & $py -m pip install --upgrade pip
    if ($LASTEXITCODE) { throw 'pip falhou' }
    & $py -m pip install -r requirements.txt pyinstaller
    if ($LASTEXITCODE) { throw 'pip install falhou' }
}

$version = (& $py -c "from app.config import APP_VERSION; print(APP_VERSION)").Trim()
if ($LASTEXITCODE) { throw 'nao consegui ler a versao' }
Write-Host "Rory Assistant $version"

& $py scripts\make_icons.py
if ($LASTEXITCODE) { throw 'make_icons falhou' }
& $py -m PyInstaller --noconfirm --distpath dist --workpath build packaging\rory-assistant.spec
if ($LASTEXITCODE) { throw 'PyInstaller falhou' }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 nao encontrado (winget install JRSoftware.InnoSetup)' }

& $iscc "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE) { throw 'Inno Setup falhou' }
Get-ChildItem dist\installer | Select-Object Name, @{n='MB'; e={[math]::Round($_.Length / 1MB, 1)}}
