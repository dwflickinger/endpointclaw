; ---------------------------------------------------------------------------
; EndpointClaw Inno Setup Script
;
; Builds a Windows installer for EndpointClaw.
; Requires Inno Setup 6.x — https://jrsoftware.org/isinfo.php
; ---------------------------------------------------------------------------

#define MyAppName      "EndpointClaw"
#define MyAppVersion   "0.1.0"
#define MyAppPublisher "Corvex Roofing Solutions"
#define MyAppURL       "https://corvexroofing.com"
#define MyAppExeName   "EndpointClaw.exe"
#define MyAppPort      "8742"

[Setup]
AppId={{E8A3F1D2-7B4C-4E5F-9A6D-1C2B3D4E5F6A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir=output
OutputBaseFilename=EndpointClaw-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
MinVersion=10.0.17763

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupentry"; Description: "Start {#MyAppName} when Windows starts"; GroupDescription: "Startup:"; Flags: checked

[Files]
; Main executable (built by PyInstaller)
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Default configuration
Source: "..\agent\config\default_config.json"; DestDir: "{app}\config"; Flags: ignoreversion

; Icon resource
Source: "resources\icon.ico"; DestDir: "{app}\resources"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{group}\{#MyAppName} Chat"; Filename: "http://localhost:{#MyAppPort}"

[Registry]
; Startup entry (only if task selected)
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startupentry

[Run]
; Launch after install (optional)
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent
; Open setup page in browser
Filename: "http://localhost:{#MyAppPort}/setup"; Description: "Open setup page in browser"; \
    Flags: nowait postinstall skipifsilent shellexec unchecked

; ---------------------------------------------------------------------------
; Custom wizard page for Company / Email input  (Pascal Script)
; ---------------------------------------------------------------------------

[Code]
var
  CompanyPage: TInputQueryWizardPage;
  CompanyEdit: TNewEdit;
  EmailEdit: TNewEdit;
  GeneratedApiKey: String;

{ Generate a simple UUID-style API key using random hex characters }
function GenerateApiKey: String;
var
  I: Integer;
  HexChars: String;
  Key: String;
begin
  HexChars := '0123456789abcdef';
  Key := 'ec-';
  for I := 1 to 32 do
  begin
    Key := Key + HexChars[Random(16) + 1];
  end;
  Result := Key;
end;

{ Create the custom Company/Email input page }
procedure InitializeWizard;
begin
  CompanyPage := CreateInputQueryPage(
    wpLicense,
    'Account Configuration',
    'Enter your company and email information.',
    'Please provide the following details to configure {#MyAppName}.'
  );

  CompanyPage.Add('Company ID:', False);
  CompanyPage.Add('Email Address:', False);

  { Set defaults }
  CompanyPage.Values[0] := 'corvex';
  CompanyPage.Values[1] := '';
end;

{ Validate the custom page inputs }
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if CurPageID = CompanyPage.ID then
  begin
    if CompanyPage.Values[0] = '' then
    begin
      MsgBox('Please enter a Company ID.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if CompanyPage.Values[1] = '' then
    begin
      if MsgBox('No email address provided. You can configure this later.' + #13#10 +
                'Continue without an email?', mbConfirmation, MB_YESNO) = IDNO then
      begin
        Result := False;
        Exit;
      end;
    end;
  end;
end;

{ Create the data directory structure }
procedure CreateDataDirectories;
var
  DataDir: String;
begin
  DataDir := ExpandConstant('{userappdata}\{#MyAppName}');

  if not DirExists(DataDir) then
    ForceDirectories(DataDir);
  if not DirExists(DataDir + '\logs') then
    ForceDirectories(DataDir + '\logs');
  if not DirExists(DataDir + '\screenshots') then
    ForceDirectories(DataDir + '\screenshots');
  if not DirExists(DataDir + '\data') then
    ForceDirectories(DataDir + '\data');

  Log('Created data directories under: ' + DataDir);
end;

{ Write the initial config.json with user-provided values }
procedure WriteInitialConfig;
var
  DataDir: String;
  ConfigPath: String;
  ConfigLines: TArrayOfString;
  ComputerName: String;
begin
  DataDir := ExpandConstant('{userappdata}\{#MyAppName}');
  ConfigPath := DataDir + '\config.json';
  GeneratedApiKey := GenerateApiKey;
  ComputerName := GetComputerNameString;

  SetArrayLength(ConfigLines, 33);
  ConfigLines[0]  := '{';
  ConfigLines[1]  := '    "supabase_url": "https://twgdhuimqspfoimfmyxz.supabase.co",';
  ConfigLines[2]  := '    "supabase_key": "",';
  ConfigLines[3]  := '    "api_key": "' + GeneratedApiKey + '",';
  ConfigLines[4]  := '    "company_id": "' + CompanyPage.Values[0] + '",';
  ConfigLines[5]  := '    "user_email": "' + CompanyPage.Values[1] + '",';
  ConfigLines[6]  := '    "device_name": "' + ComputerName + '",';
  ConfigLines[7]  := '    "anthropic_api_key": "",';
  ConfigLines[8]  := '    "chat_port": {#MyAppPort},';
  ConfigLines[9]  := '    "heartbeat_interval": 60,';
  ConfigLines[10] := '    "sync_interval": 300,';
  ConfigLines[11] := '    "command_poll_interval": 10,';
  ConfigLines[12] := '    "monitored_paths": [],';
  ConfigLines[13] := '    "monitored_extensions": [".xlsx", ".pdf", ".docx", ".dwg", ".dxf", ".jpg", ".png", ".csv", ".txt"],';
  ConfigLines[14] := '    "excluded_patterns": ["node_modules", "__pycache__", ".git", ".venv", "AppData", "ProgramData"],';
  ConfigLines[15] := '    "max_cpu_percent": 15.0,';
  ConfigLines[16] := '    "max_ram_mb": 500,';
  ConfigLines[17] := '    "screenshot_enabled": false,';
  ConfigLines[18] := '    "screenshot_interval": 300,';
  ConfigLines[19] := '    "screenshot_quality": 70,';
  ConfigLines[20] := '    "screenshot_max_age_days": 7,';
  ConfigLines[21] := '    "keystroke_enabled": false,';
  ConfigLines[22] := '    "keystroke_chunk_seconds": 30,';
  ConfigLines[23] := '    "idle_threshold_minutes": 5,';
  ConfigLines[24] := '    "log_level": "INFO",';
  ConfigLines[25] := '    "auto_update_enabled": true,';
  ConfigLines[26] := '    "auto_update_interval_hours": 24';
  ConfigLines[27] := '}';

  { Trim unused entries }
  SetArrayLength(ConfigLines, 28);

  if not SaveStringsToUTF8File(ConfigPath, ConfigLines, False) then
    Log('ERROR: Failed to write config to ' + ConfigPath)
  else
    Log('Config written to: ' + ConfigPath);
end;

{ Register EndpointClaw as a Windows service }
procedure RegisterWindowsService;
var
  ExePath: String;
  ResultCode: Integer;
begin
  ExePath := ExpandConstant('{app}\{#MyAppExeName}');

  { Remove existing service if present }
  Exec('sc.exe', 'stop ' + '{#MyAppName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('sc.exe', 'delete ' + '{#MyAppName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  { Create new service }
  if Exec('sc.exe',
          'create {#MyAppName} binPath= "\"' + ExePath + '\" --service" ' +
          'DisplayName= "{#MyAppName} Agent" start= auto',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Exec('sc.exe',
         'description {#MyAppName} "EndpointClaw local AI agent for endpoint monitoring and assistance"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Log('Windows service registered successfully');
  end
  else
    Log('WARNING: Failed to register Windows service');
end;

{ Post-installation steps }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    Log('Running post-install steps...');

    { Create data directories }
    CreateDataDirectories;

    { Write initial config }
    WriteInitialConfig;

    { Register service }
    RegisterWindowsService;

    Log('Post-install steps completed.');
    Log('Generated API Key: ' + GeneratedApiKey);
  end;
end;

{ Uninstall: stop and remove service, clean up }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { Stop the service }
    Exec('sc.exe', 'stop {#MyAppName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    { Delete the service }
    Exec('sc.exe', 'delete {#MyAppName}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

    { Remove startup registry entry }
    RegDeleteValue(HKEY_CURRENT_USER,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
      '{#MyAppName}');

    Log('Service stopped, removed, and startup entry deleted.');

    { Ask about removing data directory }
    DataDir := ExpandConstant('{userappdata}\{#MyAppName}');
    if DirExists(DataDir) then
    begin
      if MsgBox('Do you want to remove all {#MyAppName} data?' + #13#10 +
                #13#10 +
                'This includes logs, screenshots, and configuration.' + #13#10 +
                'Data directory: ' + DataDir,
                mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
        Log('Data directory removed: ' + DataDir);
      end
      else
        Log('Data directory preserved: ' + DataDir);
    end;
  end;
end;
