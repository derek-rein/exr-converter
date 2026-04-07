; Inno Setup script for Slate Maker
; Populated by CI — {#AppVersion} is injected at build time via /D flag.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{C8E4B2F3-5E6D-4F9B-A02C-3D4E5F6A7B8C}
AppName=Slate Maker
AppVersion={#AppVersion}
AppPublisher=Derek Rein
AppPublisherURL=https://github.com/derek-rein/vfx-tools
DefaultDirName={autopf}\Slate Maker
DefaultGroupName=Slate Maker
UninstallDisplayIcon={app}\slate_maker.exe
OutputBaseFilename=slate_maker-windows-x86_64-setup
OutputDir=.
Compression=lzma2/ultra64
SolidCompression=yes
SetupIconFile=public\icon.ico
ArchitecturesInstallIn64BitModeOnly=x64compatible
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Files]
Source: "dist\slate_maker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Slate Maker"; Filename: "{app}\slate_maker.exe"
Name: "{group}\Uninstall Slate Maker"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Slate Maker"; Filename: "{app}\slate_maker.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\slate_maker.exe"; Description: "Launch Slate Maker"; Flags: nowait postinstall skipifsilent
