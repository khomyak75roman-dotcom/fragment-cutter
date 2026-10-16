; Fragment Cutter — Inno Setup script
[Setup]
AppId={{8C4F2B1A-9D3E-4F6A-B7C8-1E2D3A4B5C6D}
AppName=Fragment Cutter
AppVersion=1.0.0
AppPublisher=Roman Khomyak
PrivilegesRequired=lowest
DefaultDirName={localappdata}\FragmentCutter
DefaultGroupName=Fragment Cutter
UninstallDisplayName=Fragment Cutter
OutputDir=dist
OutputBaseFilename=FragmentCutter-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\FragmentCutter.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Fragment Cutter"; Filename: "{app}\FragmentCutter.exe"
Name: "{autodesktop}\Fragment Cutter"; Filename: "{app}\FragmentCutter.exe"

[Run]
Filename: "{app}\FragmentCutter.exe"; Description: "Launch Fragment Cutter"; Flags: nowait postinstall skipifsilent
