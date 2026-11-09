# Instalador do Rory Assistant para Windows (roda do código-fonte, sem .exe).
#
#   Instalar ou atualizar:  irm https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.ps1 | iex
#   Desinstalar:            $env:RORY_UNINSTALL=1; irm https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.ps1 | iex
#
# Variáveis opcionais (como o script roda via iex, não há parâmetros):
#   RORY_UNINSTALL=1   remove o app e os atalhos (pergunta antes de apagar os dados)
#   RORY_PURGE=1|0     na desinstalação, apaga (1) ou mantém (0) os dados sem perguntar
#   RORY_DEST=<pasta>  onde instalar (padrão: %LOCALAPPDATA%\Programs\Rory Assistant)
#   RORY_REF=<tag|main> versão a baixar (padrão: a última Release; se não houver, main)
#   RORY_LOCAL_SRC=<pasta> usa o código desta pasta em vez de baixar (testes)
#   RORY_NO_SHORTCUTS=1 não cria atalhos (testes)
#   RORY_PYTHON=3.14   versão do Python que o uv instala
#
# Arquivo UTF-8 sem BOM, compatível com o PowerShell 5.1. Não usa exit: quem fecha a janela
# é o usuário (iex roda na sessão dele).

function Install-RoryAssistant {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # no PS 5.1 a barra de progresso deixa o download muito lento
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

    $repo = 'rorysystems/rory-assistant'
    $name = 'Rory Assistant'
    $dest = if ($env:RORY_DEST) { $env:RORY_DEST } else { Join-Path $env:LOCALAPPDATA 'Programs\Rory Assistant' }
    $py = if ($env:RORY_PYTHON) { $env:RORY_PYTHON } else { '3.14' }
    $src = Join-Path $dest 'src'
    $venv = Join-Path $dest 'venv'
    $dataDir = Join-Path $env:APPDATA $name
    $lnkMenu = Join-Path ([Environment]::GetFolderPath('Programs')) "$name.lnk"
    $lnkDesk = Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk"   # pode estar no OneDrive

    function Say($text) { Write-Host $text -ForegroundColor Cyan }
    function Ok($text) { Write-Host "  $text" -ForegroundColor Green }
    function Warn($text) { Write-Host "  $text" -ForegroundColor Yellow }

    function Stop-IfRunning {
        $running = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Path -and $_.Path.StartsWith($dest, [StringComparison]::OrdinalIgnoreCase)
        }
        if ($running) {
            throw "O $name está aberto. Feche a janela dele (e o ícone na bandeja, se houver) e rode o comando de novo."
        }
    }

    # ---------------------------------------------------------------- desinstalar
    if ($env:RORY_UNINSTALL -eq '1') {
        Say "Desinstalando o $name..."
        Stop-IfRunning
        foreach ($lnk in @($lnkMenu, $lnkDesk)) { if (Test-Path $lnk) { Remove-Item $lnk -Force; Ok "Atalho removido: $lnk" } }
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force; Ok "Pasta removida: $dest" }
        if (Test-Path $dataDir) {
            $purge = $env:RORY_PURGE
            if (-not $purge) {
                $answer = Read-Host "Apagar também as conversas e configurações em '$dataDir'? (s/N)"
                $purge = if ($answer -match '^[sSyY]') { '1' } else { '0' }
            }
            if ($purge -eq '1') { Remove-Item $dataDir -Recurse -Force; Ok 'Dados apagados.' } else { Ok "Dados mantidos em $dataDir" }
        }
        Say 'Pronto. (O uv e o Python que ele baixou ficam no PC; para remover: uv cache clean)'
        return
    }

    Say "Instalando o $name em $dest"
    if ($PSVersionTable.PSVersion.Major -lt 5) { throw 'Precisa do Windows PowerShell 5.1 ou mais novo.' }
    Stop-IfRunning
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    # ---------------------------------------------------------------- uv
    Say '1/5 Verificando o uv (gerenciador do Python)'
    $uv = (Get-Command uv -ErrorAction SilentlyContinue)
    if ($uv) { $uv = $uv.Source }
    if (-not $uv) {
        $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        if (Test-Path $candidate) { $uv = $candidate }
    }
    if (-not $uv) {
        Warn 'uv não encontrado: instalando pelo instalador oficial (astral.sh), sem administrador.'
        try {
            Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        } catch {
            throw "Não consegui instalar o uv ($($_.Exception.Message)). Instale manualmente (winget install astral-sh.uv) e rode de novo."
        }
        $env:Path = (Join-Path $env:USERPROFILE '.local\bin') + ';' + $env:Path
        $uv = (Get-Command uv -ErrorAction SilentlyContinue)
        if ($uv) { $uv = $uv.Source }
        if (-not $uv) { throw 'O uv foi instalado, mas não achei o uv.exe. Abra um PowerShell novo e rode o comando de novo.' }
    }
    Ok "uv: $uv"

    # ---------------------------------------------------------------- código
    Say '2/5 Baixando o código'
    $work = Join-Path ([IO.Path]::GetTempPath()) ('rory-install-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    try {
        $version = ''
        if ($env:RORY_LOCAL_SRC) {
            $fresh = $env:RORY_LOCAL_SRC
            $version = 'local'
        } else {
            $ref = $env:RORY_REF
            if (-not $ref) {
                try {
                    $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'rory-installer' }
                    $ref = $rel.tag_name
                } catch {
                    Warn 'Nenhuma Release encontrada: usando a branch main.'
                    $ref = 'main'
                }
            }
            $zip = Join-Path $work 'src.zip'
            $urls = @("https://github.com/$repo/archive/refs/tags/$ref.zip", "https://github.com/$repo/archive/refs/heads/$ref.zip")
            $got = $false
            foreach ($url in $urls) {
                try { Invoke-WebRequest $url -OutFile $zip -UseBasicParsing -Headers @{ 'User-Agent' = 'rory-installer' }; $got = $true; break } catch { }
            }
            if (-not $got -and $ref -ne 'main') {
                Warn "Versão '$ref' não encontrada: usando a branch main."
                $ref = 'main'
                try { Invoke-WebRequest "https://github.com/$repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing -Headers @{ 'User-Agent' = 'rory-installer' }; $got = $true } catch { }
            }
            if (-not $got) { throw 'Não consegui baixar o código do GitHub. Veja a internet/proxy e tente de novo.' }
            Expand-Archive -Path $zip -DestinationPath $work -Force
            $fresh = (Get-ChildItem $work -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'requirements.txt') } | Select-Object -First 1).FullName
            if (-not $fresh) { throw 'O arquivo baixado não tem o formato esperado (sem requirements.txt).' }
            $version = $ref
        }

        # troca só o código; a pasta do Python (venv) e os dados do usuário ficam
        if (Test-Path $src) { Remove-Item $src -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $src | Out-Null
        foreach ($item in 'app', 'ui', 'assets', 'scripts', 'requirements.txt', 'README.md') {
            $from = Join-Path $fresh $item
            if (Test-Path $from) { Copy-Item $from -Destination $src -Recurse -Force }
        }
        Set-Content -Path (Join-Path $dest 'versao.txt') -Value $version -Encoding ASCII
        Ok "Versão: $version"
    } finally {
        Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    }

    # ---------------------------------------------------------------- python + dependências
    Say "3/5 Preparando o Python $py e as dependências (na primeira vez baixa ~500 MB)"
    $pyExe = Join-Path $venv 'Scripts\python.exe'
    $pyw = Join-Path $venv 'Scripts\pythonw.exe'
    if (-not (Test-Path $pyExe)) {
        & $uv venv -q $venv --python $py
        if ($LASTEXITCODE -ne 0) { throw "O uv não conseguiu criar o ambiente Python $py (código $LASTEXITCODE)." }
    }
    & $uv pip install -q --python $pyExe -r (Join-Path $src 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw "A instalação das dependências falhou (código $LASTEXITCODE). Veja as mensagens acima." }
    Push-Location $src
    try {
        & $pyExe -I -c "import sys; sys.path.insert(0, '.'); import app.main"
        if ($LASTEXITCODE -ne 0) {
            throw 'O app foi instalado, mas não abre (falha ao importar). Se a mensagem acima fala em "Aplicativo bloqueou este arquivo", é o Controle Inteligente de Aplicativos: veja o README (Problemas comuns).'
        }
    } finally { Pop-Location }
    Ok 'Dependências OK.'

    # ---------------------------------------------------------------- atalhos
    Say '4/5 Criando os atalhos'
    if ($env:RORY_NO_SHORTCUTS -eq '1') {
        Warn 'RORY_NO_SHORTCUTS=1: atalhos ignorados.'
    } else {
        $shell = New-Object -ComObject WScript.Shell
        foreach ($lnk in @($lnkMenu, $lnkDesk)) {
            New-Item -ItemType Directory -Force -Path (Split-Path $lnk) | Out-Null
            $s = $shell.CreateShortcut($lnk)
            $s.TargetPath = $pyw
            $s.Arguments = '-m app.main'
            $s.WorkingDirectory = $src
            $s.IconLocation = (Join-Path $src 'assets\icon.ico')
            $s.Description = 'Assistente de desenvolvimento com Claude Code'
            $s.Save()
            Ok $lnk
        }
    }

    # ---------------------------------------------------------------- claude
    Say '5/5 Verificando o Claude Code'
    $claude = (& $pyExe -I -c "import sys; sys.path.insert(0, r'$src'); from app import config; print(config.find_claude() or '')").Trim()
    if (-not $claude) {
        Warn 'Não achei o Claude Code. Ele deveria vir junto com as dependências; rode o comando de novo.'
    } else {
        $logged = $false
        try {
            $json = (& $claude auth status 2>$null) -join "`n"
            if ($json.TrimStart().StartsWith('{')) { $logged = [bool]($json | ConvertFrom-Json).loggedIn }
        } catch { }
        if ($logged) { Ok 'Claude Code com login feito.' }
        else {
            Warn 'Falta fazer login no Claude Code (precisa de assinatura Claude). Duas opções:'
            Warn '  - abra o app: ele mostra o botão "Entrar na conta" em Configurações; ou'
            Warn "  - rode no terminal:  & '$claude' auth login"
        }
    }

    Write-Host ''
    Say "Pronto! Abra o '$name' pelo menu Iniciar ou pela Área de Trabalho."
    Write-Host "Para atualizar, rode o mesmo comando de instalação de novo. Seus dados ficam em $dataDir"
}

try {
    if ($args -contains '-Uninstall') { $env:RORY_UNINSTALL = '1' }
    Install-RoryAssistant
} catch {
    Write-Host ''
    Write-Host "ERRO: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Nada foi apagado dos seus dados. Se o erro persistir, veja "Problemas comuns" no README.' -ForegroundColor Red
}
