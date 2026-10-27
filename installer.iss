; Instalador do Remoto GF (Inno Setup)
#ifndef AppVersion
  #define AppVersion "1.0"
#endif

[Setup]
AppId={{6C1F3B0A-9E1D-4C7B-9C55-A1B2C3D4E5F6}
AppName=Remoto GF
AppVersion={#AppVersion}
AppPublisher=Gabriel Fulgoni
DefaultDirName={autopf}\Remoto GF
DefaultGroupName=Remoto GF
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=RemotoGF-Instalador
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\RemotoGF.exe
UninstallDisplayName=Remoto GF
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"
Name: "autostart"; Description: "Iniciar junto com o Windows (fica na bandeja, perto do relógio)"

[InstallDelete]
; remove arquivos de versões antigas antes de copiar os novos
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; modo pasta (onedir): o app não se extrai mais em %TEMP%\_MEI..., abre mais rápido e não deixa lixo
Source: "dist\RemotoGF\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Remoto GF"; Filename: "{app}\RemotoGF.exe"
Name: "{group}\Remoto GF (administrador)"; Filename: "{app}\RemotoGF.exe"; Parameters: "--admin"
Name: "{autodesktop}\Remoto GF"; Filename: "{app}\RemotoGF.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "RemotoGF"; ValueData: """{app}\RemotoGF.exe"" --minimizado"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
; libera o app no Firewall para o celular conseguir conectar (sem aquele aviso do Windows)
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Remoto GF"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Remoto GF"" dir=in action=allow program=""{app}\RemotoGF.exe"" enable=yes profile=any"; Flags: runhidden
; tarefa SYSTEM para desbloquear a tela de login do Windows (Winlogon) pelo celular mesmo sem UAC
Filename: "{sys}\schtasks.exe"; Parameters: "/Create /F /TN ""RemotoGF-Unlock"" /TR ""\""{app}\RemotoGF.exe\"" --unlock-worker"" /SC ONCE /ST 00:00 /RU SYSTEM /RL HIGHEST"; Flags: runhidden
; tarefa com privilégio máximo ao entrar no Windows para capturar e controlar até a tela de bloqueio
Filename: "{sys}\schtasks.exe"; Parameters: "/Create /F /TN ""RemotoGF"" /TR ""\""{app}\RemotoGF.exe\"" --minimizado"" /SC ONLOGON /RL HIGHEST"; Tasks: autostart; Flags: runhidden
Filename: "{app}\RemotoGF.exe"; Description: "Abrir o Remoto GF agora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Remoto GF"""; Flags: runhidden; RunOnceId: "fw"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /F /TN ""RemotoGF-Unlock"""; Flags: runhidden; RunOnceId: "tk_unlock"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /F /TN ""RemotoGF"""; Flags: runhidden; RunOnceId: "tk_logon"
