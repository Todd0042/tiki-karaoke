; Inno Setup Script for Tiki Karaoke Lounge
; Generates standalone turnkey installer: TikiKaraokeSetup-Pro-v1.0.5.exe or TikiKaraokeSetup-Free-v1.0.5.exe

#ifndef MyAppEdition
  #define MyAppEdition "Pro"
#endif

#define MyAppName "Tiki Karaoke Lounge"
#define MyAppVersion "1.0.5"
#define MyAppPublisher "Todd"
#define MyAppURL "https://github.com/Todd0042/tiki-karaoke"
#define MyAppExeName "TikiKaraoke.exe"

[Setup]
; Unique AppId for upgrade detection
AppId={{8F15A89B-4D4B-4811-A5C7-D57B06AE11E1}
AppName={#MyAppName} ({#MyAppEdition} Edition)
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=dist_installer
OutputBaseFilename=TikiKaraokeSetup-{#MyAppEdition}-v{#MyAppVersion}
SetupIconFile=tiki-icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=auto
CloseApplications=force
RestartApplications=no
LicenseFile=LICENSE.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

#if FileExists("C:\TikiBuild\dist\TikiKaraoke\TikiKaraoke.exe")
#define SourceDir "C:\TikiBuild\dist\TikiKaraoke"
#else
#define SourceDir "dist\TikiKaraoke"
#endif

[Files]
; Copy all compiled binaries, dependencies, static web assets, and bundled tools
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "tiki-icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "README_WINDOWS.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_LICENSES.txt"; DestDir: "{app}"; Flags: ignoreversion

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{userappdata}\TikiKaraoke"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\tiki-icon.ico"; AppUserModelID: "Todd.TikiKaraoke.Lounge.1.0"
#if FileExists(SourceDir + "\TikiKaraokeImporter.exe")
Name: "{group}\Tiki Karaoke Smart Importer"; Filename: "{app}\TikiKaraokeImporter.exe"; IconFilename: "{app}\tiki-icon.ico"; AppUserModelID: "Todd.TikiKaraoke.Importer.1.0"
#endif
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\tiki-icon.ico"; Tasks: desktopicon; AppUserModelID: "Todd.TikiKaraoke.Lounge.1.0"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteUserData: Boolean;

procedure KillTikiProcesses();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /T /IM cloudflared.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /T /IM bore.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /T /IM tiki-mic-router.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /T /IM TikiKaraokeImporter.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /T /IM TikiKaraoke.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  KillTikiProcesses();
  Sleep(250);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  KillTikiProcesses();
  Sleep(250);
end;

function InitializeUninstall(): Boolean;
var
  PromptMsg: String;
begin
  Result := True;
  DeleteUserData := False;
  
  PromptMsg := 'Do you also want to permanently delete your downloaded karaoke songs, video crates, and database in Music\Karaoke\Tiki?' + #13#10 + #13#10 +
               '• Click YES to completely remove all songs, crates, and database.' + #13#10 +
               '• Click NO to preserve your downloaded songs and song database for future use.';
               
  if MsgBox(PromptMsg, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = idYes then
  begin
    DeleteUserData := True;
  end;
end;

function GetUserMusicDir(): String;
var
  MusicDir: String;
begin
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders', 'My Music', MusicDir) then
  begin
    Result := ExpandConstant(MusicDir);
  end
  else if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders', 'My Music', MusicDir) then
  begin
    Result := ExpandConstant(MusicDir);
  end
  else
  begin
    Result := ExpandConstant('{userdocs}\..\Music');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  MusicTikiDir: String;
  KaraokeParentDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    KillTikiProcesses();
    Sleep(250);
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    // 1. Always purge AppData configuration and runtime cache
    DelTree(ExpandConstant('{userappdata}\TikiKaraoke'), True, True, True);
    
    // 2. If user requested complete purge of songs and database
    if DeleteUserData then
    begin
      MusicTikiDir := AddBackslash(GetUserMusicDir()) + 'Karaoke\Tiki';
      if DirExists(MusicTikiDir) then
      begin
        DelTree(MusicTikiDir, True, True, True);
      end;
      KaraokeParentDir := AddBackslash(GetUserMusicDir()) + 'Karaoke';
      if DirExists(KaraokeParentDir) then
      begin
        RemoveDir(KaraokeParentDir);
      end;
    end;
    
    // 3. Ensure any post-install generated files in {app} are wiped clean
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
