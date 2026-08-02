#ifndef AppVersion
  #define AppVersion "0.2.1"
#endif

[Setup]
AppId={{82CF1C8E-DF8A-4E71-B7F4-4EE7599FA0D2}
AppName=Telegram Account Orchestrator
AppVersion={#AppVersion}
AppPublisher=soulknight666
AppPublisherURL=https://github.com/soulknight666/telegram-account-orchestrator
AppSupportURL=https://github.com/soulknight666/telegram-account-orchestrator/issues
DefaultDirName={localappdata}\Programs\TAO
DefaultGroupName=Telegram Account Orchestrator
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=TAO-Windows-x64-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\build\branding\tao.ico
UninstallDisplayIcon={app}\TAO-Launcher.exe
LicenseFile=..\LICENSE

[Files]
Source: "..\dist\TAO-Windows-x64-Portable\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Telegram Account Orchestrator"; Filename: "{app}\TAO-Launcher.exe"
Name: "{autodesktop}\Telegram Account Orchestrator"; Filename: "{app}\TAO-Launcher.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Run]
Filename: "{app}\TAO-Launcher.exe"; Description: "启动 Telegram Account Orchestrator"; Flags: nowait postinstall skipifsilent
