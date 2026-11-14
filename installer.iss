; Inno Setup script for EXR Converter
; Populated by CI — {#AppVersion} is injected at build time via /D flag.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B7F3A1E2-4D5C-4E8A-9F1B-2C3D4E5F6A7B}
AppName=EXR Converter
AppVersion={#AppVersion}
AppPublisher=DerekVFX
AppPublisherURL=https://vfx.systems/docs/exr-converter
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
ChangesEnvironment=yes
ChangesAssociations=yes

[Files]
Source: "dist\exr_converter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Console-friendly launcher: waits for the app, so a terminal gets its output and exit code.
Source: "packaging\windows\exr-converter.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\EXR Converter"; Filename: "{app}\exr_converter.exe"
Name: "{group}\Uninstall EXR Converter"; Filename: "{uninstallexe}"
Name: "{autodesktop}\EXR Converter"; Filename: "{app}\exr_converter.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "associate"; Description: "Offer EXR Converter in Open With for EXR, DPX, R3D, and BRAW files"; GroupDescription: "File types:"
Name: "addtopath"; Description: "Add EXR Converter to PATH (run exr-converter from a terminal)"; GroupDescription: "Command line:"

[Registry]
; Per-user install writes the user PATH, an all-users (admin) install the system PATH.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsUserPath
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsSystemPath
; --- File types: begin (generated from src/core/file_types.py; see tests) ---
Root: HKA; Subkey: "Software\EXR Converter\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "EXR Converter"; Tasks: associate; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\EXR Converter\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "View and convert OpenEXR, DPX, and camera RAW with OpenColorIO"; Tasks: associate
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "EXR Converter"; ValueData: "Software\EXR Converter\Capabilities"; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\exr_converter.exe\shell\open\command"; ValueType: string; ValueData: """{app}\exr_converter.exe"" --view ""%1"""; Tasks: associate; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\EXRConverter.exr"; ValueType: string; ValueData: "OpenEXR image"; Tasks: associate; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\EXRConverter.exr\DefaultIcon"; ValueType: string; ValueData: "{app}\exr_converter.exe,0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.exr\shell\open"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.exr\shell\open\command"; ValueType: string; ValueData: """{app}\exr_converter.exe"" --view ""%1"""; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.exr\OpenWithProgids"; ValueType: string; ValueName: "EXRConverter.exr"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\exr_converter.exe\SupportedTypes"; ValueType: string; ValueName: ".exr"; ValueData: ""; Tasks: associate
Root: HKA; Subkey: "Software\EXR Converter\Capabilities\FileAssociations"; ValueType: string; ValueName: ".exr"; ValueData: "EXRConverter.exr"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.dpx"; ValueType: string; ValueData: "DPX image"; Tasks: associate; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\EXRConverter.dpx\DefaultIcon"; ValueType: string; ValueData: "{app}\exr_converter.exe,0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.dpx\shell\open"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.dpx\shell\open\command"; ValueType: string; ValueData: """{app}\exr_converter.exe"" --view ""%1"""; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.dpx\OpenWithProgids"; ValueType: string; ValueName: "EXRConverter.dpx"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\exr_converter.exe\SupportedTypes"; ValueType: string; ValueName: ".dpx"; ValueData: ""; Tasks: associate
Root: HKA; Subkey: "Software\EXR Converter\Capabilities\FileAssociations"; ValueType: string; ValueName: ".dpx"; ValueData: "EXRConverter.dpx"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.r3d"; ValueType: string; ValueData: "RED R3D clip"; Tasks: associate; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\EXRConverter.r3d\DefaultIcon"; ValueType: string; ValueData: "{app}\exr_converter.exe,0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.r3d\shell\open"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.r3d\shell\open\command"; ValueType: string; ValueData: """{app}\exr_converter.exe"" --view ""%1"""; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.r3d\OpenWithProgids"; ValueType: string; ValueName: "EXRConverter.r3d"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\exr_converter.exe\SupportedTypes"; ValueType: string; ValueName: ".r3d"; ValueData: ""; Tasks: associate
Root: HKA; Subkey: "Software\EXR Converter\Capabilities\FileAssociations"; ValueType: string; ValueName: ".r3d"; ValueData: "EXRConverter.r3d"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.braw"; ValueType: string; ValueData: "Blackmagic RAW clip"; Tasks: associate; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\EXRConverter.braw\DefaultIcon"; ValueType: string; ValueData: "{app}\exr_converter.exe,0"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.braw\shell\open"; ValueType: string; ValueName: "MultiSelectModel"; ValueData: "Player"; Tasks: associate
Root: HKA; Subkey: "Software\Classes\EXRConverter.braw\shell\open\command"; ValueType: string; ValueData: """{app}\exr_converter.exe"" --view ""%1"""; Tasks: associate
Root: HKA; Subkey: "Software\Classes\.braw\OpenWithProgids"; ValueType: string; ValueName: "EXRConverter.braw"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associate
Root: HKA; Subkey: "Software\Classes\Applications\exr_converter.exe\SupportedTypes"; ValueType: string; ValueName: ".braw"; ValueData: ""; Tasks: associate
Root: HKA; Subkey: "Software\EXR Converter\Capabilities\FileAssociations"; ValueType: string; ValueName: ".braw"; ValueData: "EXRConverter.braw"; Tasks: associate
; --- File types: end ---

[Run]
Filename: "{app}\exr_converter.exe"; Description: "Launch EXR Converter"; Flags: nowait postinstall skipifsilent

[Code]
const
  SystemEnvKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';

function PathHasApp(Root: Integer; SubKey: string): Boolean;
var
  Paths: string;
begin
  Result := False;
  if RegQueryStringValue(Root, SubKey, 'Path', Paths) then
    Result := Pos(';' + Lowercase(ExpandConstant('{app}')) + ';', ';' + Lowercase(Paths) + ';') > 0;
end;

function NeedsUserPath: Boolean;
begin
  Result := (not IsAdminInstallMode) and (not PathHasApp(HKCU, 'Environment'));
end;

function NeedsSystemPath: Boolean;
begin
  Result := IsAdminInstallMode and (not PathHasApp(HKLM, SystemEnvKey));
end;

procedure RemoveAppFromPath(Root: Integer; SubKey: string);
var
  Paths: string;
begin
  if not RegQueryStringValue(Root, SubKey, 'Path', Paths) then
    exit;
  Paths := ';' + Paths + ';';
  if StringChangeEx(Paths, ';' + ExpandConstant('{app}') + ';', ';', True) = 0 then
    exit;
  while (Length(Paths) > 0) and (Paths[1] = ';') do
    Delete(Paths, 1, 1);
  while (Length(Paths) > 0) and (Paths[Length(Paths)] = ';') do
    Delete(Paths, Length(Paths), 1);
  RegWriteExpandStringValue(Root, SubKey, 'Path', Paths);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveAppFromPath(HKCU, 'Environment');
    if IsAdminInstallMode then
      RemoveAppFromPath(HKLM, SystemEnvKey);
  end;
end;
