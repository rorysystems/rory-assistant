# Rory Assistant

Assistente para desenvolvedores, da [Rory Systems](https://www.rorysystems.com): uma janela de chat no desktop que usa o **Claude Code** por baixo. Funciona com a sua assinatura do Claude e não precisa de chave de API.

## Instalação

Um comando no terminal instala tudo (Python, dependências e atalho), sem administrador e sem instalador `.exe`/`.dmg`. Como o app roda direto do código-fonte, o Windows e o macOS não o tratam como "programa baixado e não assinado".

**Pré-requisitos**: uma conta Claude com assinatura (Pro, Max, Team ou Enterprise) e internet. O Claude Code já vem junto com o app; falta só fazer o login (veja abaixo).

### Windows

Abra o **PowerShell** (não precisa ser como administrador) e rode:

```powershell
irm https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.ps1 | iex
```

Instala em `%LOCALAPPDATA%\Programs\Rory Assistant` e cria os atalhos no menu Iniciar e na Área de Trabalho.

### macOS

Abra o **Terminal** e rode:

```sh
curl -fsSL https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.sh | sh
```

Instala em `~/Library/Application Support/Rory Assistant App` e cria `~/Applications/Rory Assistant.app`. Precisa de macOS 11 ou mais novo (macOS 14+ nos Macs com chip Apple; no Mac Intel o instalador usa o Python 3.13).

### Linux (Debian/Ubuntu e derivados)

```sh
curl -fsSL https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.sh | sh
```

Instala em `~/.local/share/rory-assistant`, cria o item no menu de aplicativos e o comando `rory-assistant`. A janela usa o Qt instalado pelo pip, então não precisa de GTK/WebKit. Se faltar alguma biblioteca do sistema, o instalador mostra o `sudo apt install ...` exato.

### Fazer login no Claude Code

Na primeira vez, abra o app, vá em **Configurações** (Ctrl+,) e clique em **Entrar na conta**. O instalador também mostra, no final, se o login já está feito. Quem já usa o Claude Code no terminal não precisa fazer nada: a conta é a mesma.

### Atualizar

Rode o mesmo comando de instalação de novo. Ele baixa a última versão e atualiza o que mudou; suas conversas e configurações não são tocadas.

### Desinstalar

Windows:

```powershell
$env:RORY_UNINSTALL=1; irm https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.ps1 | iex
```

macOS e Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.sh | sh -s -- --uninstall
```

Remove o app e os atalhos e **pergunta** antes de apagar as conversas e configurações (`%APPDATA%\Rory Assistant`, `~/Library/Application Support/Rory Assistant`, `~/.local/share/Rory Assistant`).

### Instaladores prontos (opção secundária)

Também há instaladores na página de [Releases](https://github.com/rorysystems/rory-assistant/releases/latest) (Windows, macOS e Linux). Eles **não são assinados**, então os sistemas avisam:

- **Windows**: o SmartScreen pode aparecer; clique em **Mais informações → Executar assim mesmo**. Com o Controle Inteligente de Aplicativos ligado, o `.exe` pode ser bloqueado: prefira o comando acima.
- **macOS**: o Gatekeeper diz que o app "está danificado" ou "não pode ser aberto". Nas versões novas o botão direito → Abrir não basta; rode `xattr -cr "/Applications/Rory Assistant.app"` ou, melhor, use o comando acima.
- **Linux**: o `.tar.gz` traz um `install.sh` próprio e exige GTK3/WebKit2GTK no sistema.

### Problemas comuns

**Windows**
- *"a execução de scripts foi desabilitada neste sistema"*: o comando acima não depende da política de execução (ele não roda um arquivo `.ps1`). Isso só aparece se você baixar o `install.ps1` e executá-lo; nesse caso rode `powershell -ExecutionPolicy Bypass -File install.ps1`.
- *"Aplicativo bloqueou este arquivo"* / `DLL load failed ... política de Controle de Aplicativo`: o **Controle Inteligente de Aplicativos (SAC)** bloqueou uma biblioteca `.pyd` nova. Veja qual em PowerShell: `Get-WinEvent -LogName 'Microsoft-Windows-CodeIntegrity/Operational' -MaxEvents 20 | Where-Object Id -in 3033,3034,3077 | Format-List TimeCreated, Message`. Saídas: desligar o SAC (Segurança do Windows → Controle de aplicativo e navegador → Controle Inteligente de Aplicativos; só dá para religar reinstalando o Windows) ou esperar a biblioteca ganhar reputação. Mande o nome do arquivo bloqueado para abrirmos uma issue.
- *SmartScreen*: não aparece neste método (o instalador é um script e não há `.exe` novo). Se aparecer ao abrir o atalho, clique em **Mais informações → Executar assim mesmo**.
- *"O Rory Assistant está aberto"* ao atualizar: feche a janela do app e rode o comando de novo.

**macOS**
- Se o sistema pedir as **Ferramentas de Linha de Comando do Xcode** (`xcode-select --install`), aceite e rode o comando de novo.
- **Microfone (ditado)**: se o ditado não funcionar, abra **Ajustes do Sistema → Privacidade e Segurança → Microfone** e libere o app. Se ele não aparecer na lista, libere o **Terminal** e abra o app de novo.
- O `Rory Assistant.app` não abre: rode no Terminal `"$HOME/Library/Application Support/Rory Assistant App/venv/bin/python" -m app.main` (dentro de `.../src`) para ver o erro, ou rode o comando de instalação de novo (recria o `.app`).

**Linux**
- *Faltam bibliotecas* (`libxcb-cursor0`, `libegl1`, `libnss3`...): o instalador lista o comando; copie e rode o `sudo apt install ...`.
- O atalho não aparece no menu: faça logout/login ou rode `update-desktop-database ~/.local/share/applications`. Sempre dá para abrir pelo terminal com `rory-assistant` (se `~/.local/bin` não estiver no PATH, use `~/.local/share/rory-assistant/rory-assistant`).
- Em Wayland puro sem XWayland, rode com `QT_QPA_PLATFORM=wayland rory-assistant`.

**Todos os sistemas**
- *Claude Code sem login*: **Configurações → Entrar na conta**, ou no terminal o comando `... auth login` que o instalador mostra.
- *Onde fica o log*: `crash.log` na pasta de dados (`%APPDATA%\Rory Assistant`, `~/Library/Application Support/Rory Assistant` ou `~/.local/share/Rory Assistant`).
- *Reinstalar do zero*: desinstale (comando acima, mantendo os dados), apague a pasta de instalação se sobrar algo e rode o comando de instalação. Para refazer também o Python: apague a pasta `venv` dentro da pasta de instalação.

### O que muda fora do Windows

Funciona em todos: conversas, anexos, PDF/Office/CSV, áudio e vídeo (transcrição), `!comando` (bash no lugar do PowerShell), explorador de arquivos. No macOS e no Linux **não há**: o botão flutuante, o OCR do Windows (PDF escaneado vai como imagem e o texto do vídeo não é lido), a liberação automática do microfone no WebView e o agrupamento do ícone na barra de tarefas. O app é testado no Windows; **macOS e Linux ainda não foram testados em máquina real**, então relate problemas nas [issues](https://github.com/rorysystems/rory-assistant/issues).

## Por que usar em vez do terminal

Tudo o que você já usa no Claude Code do terminal continua aqui: as mesmas skills, os `/comandos`, os modos de permissão, as mensagens enviadas no meio do trabalho e o `!comando` para rodar algo no shell. Além disso, o Rory Assistant traz:

### Imagens e arquivos
- **Cole (Ctrl+V) ou arraste imagens** e veja a prévia antes de enviar. O Claude recebe a imagem direto, sem você ter que salvar o arquivo e passar o caminho.
- **PDF, Word, Excel, PowerPoint e CSV** são convertidos para texto em Markdown no seu PC, sem gastar tokens nessa conversão. O Claude recebe só o texto, que custa bem menos tokens do que o arquivo original. Páginas de PDF escaneadas, sem texto, vão como imagem.
- **Áudio e vídeo** são transcritos no seu PC, sem gastar tokens. Do vídeo, ele também lê o texto que aparece na tela.
- **Ditado por voz**: fale no microfone e o texto aparece na caixa de mensagem, também transcrito no PC.
- **Explorador de arquivos ao lado do chat** (botão **Arquivos** ou Ctrl+E), como no VS Code: um visualizador e a árvore da pasta do projeto, com a tela dividida entre a conversa e os arquivos (arraste a divisória para ajustar).
  - **Markdown já formatado**: o Claude vive gerando `.md` (planos, relatórios, documentação). No terminal você vê o texto cru; aqui ele aparece com títulos, tabelas, listas e código colorido. Um botão alterna para o código.
  - **Código com cores e números de linha**, imagens, e até **PDF, Word, Excel e PowerPoint** convertidos para leitura.
  - **Atualiza sozinho** depois de cada resposta, então você vê na hora o que o Claude mudou.
  - Botões para **citar o arquivo na mensagem** (`@caminho`), copiar, abrir no VS Code ou no programa padrão.
  - Pelo menu ⋮, **converta qualquer arquivo (PDF, Word, Excel…) para `.md`** e ele já abre no visualizador.

### Histórico que não se perde
- **Todas as conversas ficam salvas e organizadas por pasta de projeto** na barra lateral. Volte a qualquer uma quando quiser, com o histórico inteiro na tela.
- O Claude **lembra da conversa** mesmo depois de fechar e reabrir o app.
- **Compactar o contexto não apaga o que você vê**: a conversa continua inteira na tela, só aparece uma marca de onde o contexto foi resumido.
- O app **abre na última conversa** em que você estava. Dá para renomear e apagar conversas.
- **Várias conversas trabalham ao mesmo tempo**: deixe o Claude rodando numa e vá para outra.

### Você sempre sabe o que está acontecendo
- **Pedidos de permissão em cartões** (Permitir / Sempre / Negar), com o motivo do pedido. Quando o Claude está parado esperando você, um aviso aparece na tela e a conversa ganha ⏸ na barra lateral.
- **Os subagentes aparecem na tela** com a tarefa de cada um e o andamento.
- Respostas com **Markdown formatado e código com destaque de sintaxe**.

### Economia da sua cota
- **Compactação automática do contexto** em segundo plano, logo depois de cada resposta, quando ela custa menos.
- **Barra de contexto** no topo da conversa: um clique compacta na hora.
- **Painel de uso**: barras da sessão de 5 h e da semana, tokens gastos e quanto isso custaria na API.

### Mais prático
- Comandos que no terminal abrem telas interativas (`/login`, `/model`, `/usage` e outros) **abrem a tela certa do app**.
- Login, troca de conta e status da conta Claude ficam nas Configurações.
- Tema claro e escuro com um clique, no botão ao lado do nome do app.
- Renomeie uma conversa clicando no título dela ou pelo menu ⋮ da conversa na barra lateral.
- `!comando` com **sugestões enquanto você digita**: comandos, parâmetros, arquivos, branches do git e o seu histórico do PowerShell.

## Documentação

- [Funcionalidades](docs/funcionalidades.md): o que cada recurso faz.
- [Arquitetura](docs/arquitetura.md): como o app funciona por dentro.
- [Desenvolvimento](docs/desenvolvimento.md): rodar, testar, gerar instaladores e pegadinhas.

## Rodar a partir do código

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m app.main
```

## Gerar os instaladores (para mantenedores)

- **Windows**: `powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1`. O instalador vai para `dist\installer`. Precisa do Inno Setup.
- **Todos os sistemas**: crie uma tag `v*` (ex.: `git tag v1.0.0 && git push --tags`). O GitHub Actions gera os três instaladores e publica na Release.

---

© Rory Systems
