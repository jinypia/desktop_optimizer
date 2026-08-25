; Inno Setup script for Desktop Optimizer.
;
; Per-user install by design: no administrator rights required, which
; matters on locked-down corporate machines. Installs to
; %LOCALAPPDATA%\Programs\Desktop Optimizer, registers a proper uninstaller
; (visible in Settings > Apps), and optionally starts with Windows.
;
; Build:  ISCC.exe packaging\installer.iss
; Expects PyInstaller output in dist\DesktopOptimizer\ (see build.ps1).

#define AppName "Desktop Optimizer"
; build.ps1 passes /DAppVersion=<app/version.py> so the installer can never
; disagree with what the app reports about itself. The fallback below only
; applies when compiling this script by hand.
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#define AppPublisher "jinypia"
#define AppURL "https://github.com/jinypia/desktop_optimizer"
#define AppExeName "DesktopOptimizer.exe"

[Setup]
AppId={{8E4C1F2A-9B7D-4E63-A5C8-2D6F0B31E7A4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}

; --- no-admin, per-user installation ---
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

; --- output ---
OutputDir=..\dist\installer
OutputBaseFilename=DesktopOptimizer-{#AppVersion}-setup
SetupIconFile=..\assets\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile=..\LICENSE
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"
Name: "startupicon"; Description: "Start {#AppName} when I sign in \
(it lives in the notification area)"; \
    GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\DesktopOptimizer\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Logs and the instance lock are written at runtime, not by the installer.
Type: filesandordirs; Name: "{localappdata}\DesktopOptimizer\logs"
Type: dirifempty; Name: "{localappdata}\DesktopOptimizer"

[Registry]
; Window/mini-strip preferences (QSettings). Removed on uninstall so the
; app leaves nothing behind.
Root: HKCU; Subkey: "Software\jinypia\DesktopOptimizer"; \
    Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\jinypia"; \
    Flags: uninsdeletekeyifempty
