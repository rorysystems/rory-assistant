; Instalador Windows do Rory Assistant (Inno Setup 6).
; Gerar:  packaging\build-windows.ps1   (roda o PyInstaller e depois este script)
; Instala por usuário, sem pedir admin, em %LOCALAPPDATA%\Programs\Rory Assistant
; (o botão flutuante procura o exe lá).

#define AppName "Rory Assistant"
#ifndef AppVersion
  #define AppVersion "1.0.2"
#endif
#define AppExe "Rory Assistant.exe"

[Setup]
AppId={{6E4F2B1C-6A51-4C3B-9C0E-52A7D1B3F0A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Rory Systems
AppPublisherURL=https://rorysystems.com
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=RoryAssistant-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"
Name: "startup"; Description: "Abrir junto com o Windows"; GroupDescription: "Inicialização:"; Flags: unchecked

[Files]
Source: "..\dist\Rory Assistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Avisa (sem bloquear) se o Claude Code não estiver instalado.
function ClaudeInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{%USERPROFILE}\.local\bin\claude.exe'))
    or FileExists(ExpandConstant('{%APPDATA}\npm\claude.cmd'))
    or FileExists(ExpandConstant('{%LOCALAPPDATA}\Programs\claude\claude.exe'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not ClaudeInstalled()) and (not WizardSilent()) then
    MsgBox('O Rory Assistant usa o Claude Code, que não foi encontrado neste computador.' + #13#10#13#10 +
           'Instale pelo PowerShell com:' + #13#10 +
           '    irm https://claude.ai/install.ps1 | iex' + #13#10#13#10 +
           'Depois abra o app: ele mostra o botão de login.', mbInformation, MB_OK);
end;
