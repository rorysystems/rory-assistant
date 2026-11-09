# Funcionalidades (documentação funcional)

O que o usuário vê e faz no Rory Assistant. Detalhes de implementação: [arquitetura.md](arquitetura.md).

## Conversas

- **Nova conversa** (Ctrl+N ou botão na barra lateral): escolhe a pasta do projeto (pasta do usuário ou recentes). O Claude trabalha nessa pasta.
- **Grupos de conversas** (botão de pasta com + ao lado da busca): pastas que você cria para organizar por cliente ou produto (ex.: "Vinci" com as conversas de cada sistema dele). Grupos podem ter subgrupos (⋮ no grupo › Novo subgrupo). Clique no grupo para recolher/abrir. **Arraste** conversas para um grupo, e grupos para dentro de outros; soltar na área fora dos grupos tira do grupo. ⋮ no grupo: renomear, novo subgrupo, tirar do grupo pai, apagar. Apagar um grupo não apaga nada além dele: as conversas e os subgrupos sobem para o grupo pai (ou voltam para a lista, se ele era de nível raiz). ⋮ na conversa › "Mover para: …", "Mover para um novo grupo…" ou "Tirar do grupo". Conversa nova numa pasta que já está num grupo entra no mesmo grupo. Quem não está em grupo aparece solto, abaixo dos grupos.
- **Barra lateral**: todas as conversas, agrupadas por pasta, com busca (na busca, os grupos com resultado abrem sozinhos). ⋮ em cada conversa: renomear, abrir pasta, continuar no terminal, apagar. Bolinha = trabalhando; ⏸ = esperando permissão.
- **Renomear**: clique no título no topo, ou ⋮ da conversa.
- O app abre na última conversa; o Claude lembra de tudo mesmo depois de fechar.
- **Várias conversas ao mesmo tempo**: dá para deixar uma trabalhando e ir para outra.
- **Continuar no terminal** (menu ⋮): abre o Claude Code no terminal na mesma sessão.

## Mensagens

- Enter envia, Shift+Enter quebra linha. ↑/↓ na caixa: mensagens já enviadas.
- **Mandar no meio do trabalho**: a mensagem vai na hora, como no terminal.
- **Parar**: Esc, Ctrl+C (sem texto selecionado) ou o botão ■.
- **Anexos**: Ctrl+V, arrastar ou clipe. Imagens com prévia; PDF e Office convertidos para texto no PC; áudio e vídeo transcritos no PC.
- **Microfone**: grava, transcreve no PC e põe o texto na caixa (ou envia direto, conforme a configuração).
- **`/comandos` e skills**: digite `/` para ver a lista. Comandos que no terminal abrem telas (`/login`, `/model`, `/usage`…) abrem a tela do app.
- **`/mcp`**: abre a lista de servidores MCP da pasta da conversa (estado, tipo/URL, ferramentas) com **Autenticar** (abre o navegador para o login e acompanha até conectar), **Reconectar** e **Ativar/Desativar**.
- **`!comando`**: roda no PowerShell da pasta da conversa sem gastar tokens. Mostra sugestões ao digitar (Tab completa, ↑/↓ escolhe, Enter roda). A saída vai como contexto junto da próxima mensagem.

## Acompanhar o trabalho

- **Linha de status** na resposta: o que o Claude está fazendo e há quanto tempo.
- **Permissões**: cartão com Permitir / Sempre / Negar e o motivo. Se o Claude está parado esperando, aparece uma barra acima da caixa com "Ver pedido".
- **Modo de permissões** embaixo da caixa (Shift+Tab alterna): perguntar → aceitar edições → plano → liberar tudo.
- **Subagentes**: cada um aparece com a tarefa e o andamento.

## Arquivos do projeto

- **Explorador** (Ctrl+E ou botão **Arquivos** no topo): visualizador ao lado do chat e árvore da pasta na ponta direita. Arraste as divisórias para ajustar. Abre sozinho com a janela larga e lembra se você fechou.
- Visualizador: Markdown formatado (botão alterna para o código), código com cores e números de linha, imagens, PDF/Office convertidos.
- Atualiza sozinho depois de cada resposta.
- Botões: citar o arquivo na mensagem (`@caminho`), copiar, abrir no VS Code, abrir no programa padrão.
- **Converter arquivo para .md** (menu ⋮): salva o `.md` ao lado do original e abre no visualizador.

## Economia de cota

- **Barra de contexto** no topo: quanto da janela a conversa ocupa. Clique para compactar.
- **Compactação automática** em segundo plano depois da resposta, quando a conversa passa do limite configurado. A tela continua mostrando tudo.

## Configurações (Ctrl+, ou rodapé da barra lateral)

- **Conta Claude Code**: status, entrar, trocar de conta, sair (sair vale para o PC inteiro).
- **Uso do Claude**: barras da sessão de 5 h e da semana, tokens e custo estimado.
- **Contexto das conversas**: limite da compactação automática, subagentes, conectores do claude.ai.
- **Mídia e voz**: modo das imagens (imagem / OCR / ambos), OCR de vídeo, modelo e idioma da transcrição, envio automático do ditado.
- **Sobre**: versão e site.
- **Tema claro/escuro**: botão sol/lua ao lado do nome do app.

## Novidades da revisão
- **Plano**: card próprio com o plano completo e 3 opções (teclas 1/2/3): aceitar edições automaticamente, aprovar manualmente, ou continuar planejando com um comentário. Aviso "Modo planejamento" perto do campo.
- **Modelos**: em Configurações, "Modelo do agente" e "Modelo dos subagentes" (automático = um degrau abaixo). `/model <modelo>` troca o da conversa; `/model sub <modelo|auto>` o dos subagentes. Também há "Esforço" (baixo/médio/alto): `/effort baixo|médio|alto|auto` muda essa mesma preferência. `/rename <nome>` renomeia a conversa na barra lateral (sem nome, edita o título no topo). Mudanças valem a partir da próxima mensagem.
- **Caminhos**: clique direito em arquivo/pasta ou botão ⋯ no visualizador: copiar caminho, caminho relativo, mostrar no Explorer/Finder.
- **`!comando`**: chip com o tamanho do contexto (descartável) e botão para parar.
- **Anexos grandes** viram prévia + caminho, com "~N tokens" no chip. Histórico longo carrega as últimas 40 mensagens ("Carregar anteriores").

## Por sistema operacional
- **Instalação**: um comando no terminal (`install.ps1` no Windows, `install.sh` no macOS/Linux) instala e atualiza; ver README. O Claude Code vem embutido; só falta o login.
- **Windows**: tudo disponível (botão flutuante, OCR do Windows, microfone liberado automaticamente, instância única com foco na janela aberta).
- **macOS e Linux**: sem botão flutuante, sem OCR do Windows (PDF escaneado vai como imagem; texto de vídeo não é lido), microfone sem liberação automática (o sistema/WebView pergunta). `!comando` roda no `bash`. Instância única por trava de arquivo (a segunda abertura apenas sai). No Linux a janela usa Qt (WebEngine) e pode exigir pacotes do sistema.
