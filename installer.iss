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

[Files]
Source: "dist\RemotoGF.exe"; DestDir: "{app}"; Flags: ignoreversion

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
Filename: "{app}\RemotoGF.exe"; Description: "Abrir o Remoto GF agora"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Remoto GF"""; Flags: runhidden; RunOnceId: "fw"
