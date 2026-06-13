; Inno Setup script for EXR Converter
; Populated by CI — {#AppVersion} is injected at build time via /D flag.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B7F3A1E2-4D5C-4E8A-9F1B-2C3D4E5F6A7B}
AppName=EXR Converter
AppVersion={#AppVersion}
AppPublisher=Derek Rein
AppPublisherURL=https://github.com/derek-rein/vfx-tools
DefaultDirName={autopf}\EXR Converter
DefaultGroupName=EXR Converter
UninstallDisplayIcon={app}\exr_converter.exe
OutputBaseFilename=exr_converter-windows-x86_64-setup
OutputDir=.
Compression=lzma2/ultra64
SolidCompression=yes
SetupIconFile=resources\icons\icon.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Files]
Source: "dist\exr_converter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EXR Converter"; Filename: "{app}\exr_converter.exe"
Name: "{group}\Uninstall EXR Converter"; Filename: "{uninstallexe}"
Name: "{autodesktop}\EXR Converter"; Filename: "{app}\exr_converter.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\exr_converter.exe"; Description: "Launch EXR Converter"; Flags: nowait postinstall skipifsilent
