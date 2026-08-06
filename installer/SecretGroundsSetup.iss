#define AppName "Secret Grounds"
#ifndef AppVersion
  #define AppVersion "1.0.0-rc.2"
#endif
#define SetupName "SecretGroundsSetup-" + AppVersion

[Setup]
AppId={{A352DF81-598E-41D4-A463-7A2D7FCEB7E3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Secret Grounds
DefaultDirName={autopf}\Secret Grounds
DefaultGroupName=Secret Grounds
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename={#SetupName}
Compression=zip
SolidCompression=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
UninstallDisplayIcon={app}\VERSION
SetupLogging=yes
VersionInfoVersion=1.0.0.0
VersionInfoTextVersion={#AppVersion}
VersionInfoDescription=Secret Grounds Setup
VersionInfoCopyright=Secret Grounds

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Dirs]
Name: "{app}\config"; Check: IsCashier
Name: "{app}\data"; Check: IsCashier
Name: "{app}\backups"; Check: IsCashier
Name: "{app}\logs"
Name: "{app}\client"; Check: IsClient

[Files]
Source: "..\VERSION"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ARCHITECTURE.md"; DestDir: "{app}"; Flags: ignoreversion; Check: IsCashier
Source: "..\config\.env.example"; DestDir: "{app}\config"; Flags: ignoreversion; Check: IsCashier
Source: "..\server\*.py"; DestDir: "{app}\server"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: IsCashier
Source: "..\runtime\python\*"; DestDir: "{app}\runtime\python"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "__pycache__\*,*.pyc"; Check: IsCashier
Source: "..\runtime\node\*"; DestDir: "{app}\runtime\node"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: IsCashier
Source: "..\dashboard\.next\standalone\*"; DestDir: "{app}\dashboard\.next\standalone"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: IsCashier
Source: "..\operator-shortcuts\*"; DestDir: "{app}\operator-shortcuts"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: IsCashier
Source: "..\scripts\secret-grounds-common.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\scripts\stop-all-for-uninstall.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\scripts\start-secret-grounds.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\start-backend.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\start-dashboard.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\stop-secret-grounds.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\restart-secret-grounds.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\status-secret-grounds.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\check-deployment.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\initialize-cashier-config.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\install-cashier-role.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\install-cashier-autostart.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\remove-cashier-autostart.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\install-backend-firewall-rule.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\open-dashboard.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\install-startup-folder-shortcuts.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\remove-client-autostart.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\scripts\remove-client-startup-folder-shortcut.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsCashier
Source: "..\dist\SecretGroundsClient\*"; DestDir: "{app}\client"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: IsClient
Source: "..\client\config.example.json"; DestDir: "{app}\client"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\start-client.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\configure-client.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\client-config-tool.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\install-client-role.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\install-client-autostart.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\remove-client-autostart.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\install-startup-folder-shortcuts.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\remove-client-startup-folder-shortcut.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\client-config-tool.ps1"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\scripts\read-client-config-status.ps1"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\scripts\read-cashier-status.ps1"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{group}\Secret Grounds Dashboard"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\open-dashboard.ps1"""; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 14; Check: IsCashier
Name: "{group}\Start Secret Grounds"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\start-secret-grounds.ps1"""; WorkingDir: "{app}"; Check: IsCashier
Name: "{group}\Stop Secret Grounds"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\stop-secret-grounds.ps1"""; WorkingDir: "{app}"; Check: IsCashier
Name: "{group}\Restart Secret Grounds"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\restart-secret-grounds.ps1"""; WorkingDir: "{app}"; Check: IsCashier
Name: "{group}\Secret Grounds Status"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\status-secret-grounds.ps1"""; WorkingDir: "{app}"; Check: IsCashier
Name: "{commondesktop}\Secret Grounds Dashboard"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\open-dashboard.ps1"""; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 14; Check: IsCashier
Name: "{group}\Configure Secret Grounds Client"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\configure-client.ps1"""; WorkingDir: "{app}"; Check: IsClient

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\configure-client.ps1"" -Mode Preserve -ClientConfigPath ""{app}\client\config.json"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Preserving existing client configuration..."; Check: IsClientPreserve
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\configure-client.ps1"" -Mode {code:GetConfigMode} -ServerHost ""{code:GetClientServerHost}"" -ServerPort {code:GetClientServerPort} -StationId ""{code:GetClientStationId}"" -DisplayName ""{code:GetClientDisplayName}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Saving validated client configuration..."; Check: IsClientOnline
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\configure-client.ps1"" -Mode {code:GetConfigMode} -ServerHost ""{code:GetClientServerHost}"" -ServerPort {code:GetClientServerPort} -StationId ""{code:GetClientStationId}"" -DisplayName ""{code:GetClientDisplayName}"" -AllowOffline"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Saving offline client configuration..."; Check: IsClientOffline
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\install-client-autostart.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Installing client autostart..."; Check: ShouldInstallClientAutostart
Filename: "http://127.0.0.1:3001"; Description: "Open Secret Grounds Dashboard"; Flags: postinstall shellexec skipifsilent; Check: CashierServicesAreHealthy

[UninstallRun]
; Belt-and-suspenders re-run of the same stop-everything check that
; InitializeUninstall already gated on: the "Keep shop data?" confirmation
; dialog between InitializeUninstall and here can sit open indefinitely, long
; enough in principle for a Task Scheduler trigger to relaunch something.
; Removing the scheduled tasks right after this closes that window for good.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\stop-all-for-uninstall.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\remove-cashier-autostart.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\remove-client-autostart.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist

[Code]
var
  RolePage: TInputOptionWizardPage;
  ClientPage: TInputQueryWizardPage;
  ClientOptionsPage: TInputOptionWizardPage;
  CashierOptionsPage: TInputOptionWizardPage;
  SelectedRole: String;
  RetainShopData: Boolean;
  ExistingConfigStatus: String;
  ExistingServerHost: String;
  ExistingServerPort: String;
  ExistingStationId: String;
  ExistingDisplayName: String;
  ExistingConfigError: String;
  ExistingConfigBackupPath: String;
  ConfigModeValue: String;
  PreviousInstallLocation: String;
  ExistingCashierConfigFound: Boolean;
  ExistingCashierAutostartEnabled: Boolean;
  CashierServicesHealthy: Boolean;

function IsCashier: Boolean;
begin
  Result := SelectedRole = 'cashier';
end;

function IsClient: Boolean;
begin
  Result := SelectedRole = 'client';
end;

function IsClientPreserve: Boolean;
begin
  Result := IsClient and (ConfigModeValue = 'Preserve');
end;

function IsClientOffline: Boolean;
begin
  Result := IsClient and (ConfigModeValue <> 'Preserve') and ClientOptionsPage.Values[1];
end;

function IsClientOnline: Boolean;
begin
  Result := IsClient and (ConfigModeValue <> 'Preserve') and not ClientOptionsPage.Values[1];
end;

function GetConfigMode(Value: String): String;
begin
  Result := ConfigModeValue;
end;

function ShouldInstallClientAutostart: Boolean;
begin
  Result := IsClient and ClientOptionsPage.Values[0];
end;

function GetClientServerHost(Value: String): String;
begin
  Result := ClientPage.Values[2];
end;

function GetClientServerPort(Value: String): String;
begin
  Result := ClientPage.Values[3];
end;

function GetClientStationId(Value: String): String;
begin
  Result := ClientPage.Values[0];
end;

function GetClientDisplayName(Value: String): String;
begin
  Result := ClientPage.Values[1];
end;

function JsonValue(Source: String; Name: String): String;
var
  Pattern: String;
  StartPos: Integer;
  EndPos: Integer;
begin
  Result := '';
  Pattern := '"' + Name + '":"';
  StringChangeEx(Source, ' ', '', True);
  StartPos := Pos(Pattern, Source);
  if StartPos = 0 then Exit;
  StartPos := StartPos + Length(Pattern);
  EndPos := StartPos;
  while (EndPos <= Length(Source)) and (Source[EndPos] <> '"') do
    EndPos := EndPos + 1;
  Result := Copy(Source, StartPos, EndPos - StartPos);
end;

function JsonBool(Source: String; Name: String): Boolean;
var
  Pattern: String;
begin
  StringChangeEx(Source, ' ', '', True);
  Pattern := '"' + Name + '":true';
  Result := Pos(Pattern, Lowercase(Source)) > 0;
end;

function ValidateClientOnline(ServerHost: String; ServerPort: String; StationId: String): Boolean;
var
  Http: Variant;
  HealthUrl: String;
  CheckUrl: String;
  Version: String;
begin
  Result := False;
  HealthUrl := 'http://' + ServerHost + ':' + ServerPort + '/healthz';
  try
    Http := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Http.Open('GET', HealthUrl, False);
    Http.SetTimeouts(3000, 3000, 3000, 5000);
    Http.Send('');
    if Http.Status <> 200 then begin
      MsgBox('Secret Grounds server returned HTTP ' + IntToStr(Http.Status) + ' from /healthz.', mbError, MB_OK);
      Exit;
    end;
    Version := JsonValue(Http.ResponseText, 'version');
    if Version <> '{#AppVersion}' then begin
      MsgBox('Version mismatch. Server is ' + Version + '; installer is {#AppVersion}.', mbError, MB_OK);
      Exit;
    end;

    CheckUrl := 'http://' + ServerHost + ':' + ServerPort + '/client-registration/check?pc_name=' + StationId;
    Http.Open('GET', CheckUrl, False);
    Http.Send('');
    if Http.Status <> 200 then begin
      MsgBox('Station ID check returned HTTP ' + IntToStr(Http.Status) + '.', mbError, MB_OK);
      Exit;
    end;
    if not JsonBool(Http.ResponseText, 'available') then begin
      MsgBox('Station ID is not available. Choose a unique station ID.', mbError, MB_OK);
      Exit;
    end;
    Result := True;
  except
    MsgBox('Could not reach ' + HealthUrl + '. Check the cashier address, port, and Private-network firewall rule, or select offline preparation.', mbError, MB_OK);
  end;
end;

procedure ParseConfigStatusLine(Line: String);
var
  EqPos: Integer;
  Key, Value: String;
begin
  EqPos := Pos('=', Line);
  if EqPos = 0 then Exit;
  Key := Copy(Line, 1, EqPos - 1);
  Value := Copy(Line, EqPos + 1, Length(Line) - EqPos);
  if Key = 'STATUS' then ExistingConfigStatus := Value
  else if Key = 'SERVER_HOST' then ExistingServerHost := Value
  else if Key = 'SERVER_PORT' then ExistingServerPort := Value
  else if Key = 'PC_NAME' then ExistingStationId := Value
  else if Key = 'DISPLAY_NAME' then ExistingDisplayName := Value
  else if Key = 'ERROR' then ExistingConfigError := Value
  else if Key = 'BACKUP_PATH' then ExistingConfigBackupPath := Value;
end;

function GetPreviousInstallLocation: String;
var
  InstallLocation: String;
begin
  Result := '';
  // The app directory constant cannot be expanded this early (InitializeSetup runs
  // before the destination directory is known), so read the previous install location
  // directly from the uninstall registry key that Setup itself always writes, keyed
  // by the fixed AppId.
  if RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A352DF81-598E-41D4-A463-7A2D7FCEB7E3}_is1', 'InstallLocation', InstallLocation) then begin
    if (Length(InstallLocation) > 3) and (InstallLocation[Length(InstallLocation)] = '\') then
      InstallLocation := Copy(InstallLocation, 1, Length(InstallLocation) - 1);
    Result := InstallLocation;
  end;
end;

procedure DetectExistingClientConfig;
var
  ConfigPath, ScriptPath, TempOut, CmdParams: String;
  ResultCode: Integer;
  Lines: TArrayOfString;
  I: Integer;
  ExtractedOk: Boolean;
begin
  ExistingConfigStatus := 'NotFound';
  ExistingServerHost := '';
  ExistingServerPort := '';
  ExistingStationId := '';
  ExistingDisplayName := '';
  ExistingConfigError := '';
  ExistingConfigBackupPath := '';

  PreviousInstallLocation := GetPreviousInstallLocation();
  if PreviousInstallLocation = '' then Exit;

  ConfigPath := PreviousInstallLocation + '\client\config.json';
  if not FileExists(ConfigPath) then Exit;

  ExtractedOk := True;
  try
    ExtractTemporaryFile('client-config-tool.ps1');
    ExtractTemporaryFile('read-client-config-status.ps1');
  except
    ExtractedOk := False;
  end;
  if not ExtractedOk then begin
    ExistingConfigStatus := 'Unknown';
    Exit;
  end;

  ScriptPath := ExpandConstant('{tmp}') + '\read-client-config-status.ps1';
  TempOut := ExpandConstant('{tmp}') + '\sg-config-status.txt';
  CmdParams := '/C powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath +
    '" -ConfigPath "' + ConfigPath + '" > "' + TempOut + '" 2>&1';

  if Exec('cmd.exe', CmdParams, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin
    if FileExists(TempOut) and LoadStringsFromFile(TempOut, Lines) then begin
      for I := 0 to GetArrayLength(Lines) - 1 do
        ParseConfigStatusLine(Lines[I]);
    end;
  end;
end;

procedure DetectExistingCashierState;
var
  ScriptPath, TempOut, CmdParams: String;
  ResultCode: Integer;
  Lines: TArrayOfString;
  I, EqPos: Integer;
  Key, Value: String;
  ExtractedOk: Boolean;
begin
  ExistingCashierConfigFound := False;
  ExistingCashierAutostartEnabled := False;

  if PreviousInstallLocation = '' then Exit;
  if not FileExists(PreviousInstallLocation + '\config\.env') then Exit;

  ExistingCashierConfigFound := True;

  ExtractedOk := True;
  try
    ExtractTemporaryFile('read-cashier-status.ps1');
  except
    ExtractedOk := False;
  end;
  if not ExtractedOk then Exit;

  ScriptPath := ExpandConstant('{tmp}') + '\read-cashier-status.ps1';
  TempOut := ExpandConstant('{tmp}') + '\sg-cashier-status.txt';
  CmdParams := '/C powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath +
    '" -InstallPath "' + PreviousInstallLocation + '" > "' + TempOut + '" 2>&1';

  if Exec('cmd.exe', CmdParams, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then begin
    if FileExists(TempOut) and LoadStringsFromFile(TempOut, Lines) then begin
      for I := 0 to GetArrayLength(Lines) - 1 do begin
        EqPos := Pos('=', Lines[I]);
        if EqPos = 0 then Continue;
        Key := Copy(Lines[I], 1, EqPos - 1);
        Value := Copy(Lines[I], EqPos + 1, Length(Lines[I]) - EqPos);
        if Key = 'AUTOSTART' then
          ExistingCashierAutostartEnabled := (Lowercase(Value) = 'true');
      end;
    end;
  end;
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  DetectExistingClientConfig;
  DetectExistingCashierState;

  if ExistingConfigStatus = 'Ok' then begin
    SelectedRole := 'client';
    ConfigModeValue := 'Preserve';
  end else begin
    ConfigModeValue := 'Fresh';
    if ExistingCashierConfigFound then
      SelectedRole := 'cashier';
  end;

  if ExistingConfigStatus = 'Invalid' then begin
    if ExistingConfigBackupPath <> '' then
      MsgBox('An existing Secret Grounds client configuration was found at ' + PreviousInstallLocation + '\client\config.json' +
        ' but it could not be parsed: ' + ExistingConfigError + #13#10#13#10 +
        'Setup backed it up to:' + #13#10 + ExistingConfigBackupPath + #13#10#13#10 +
        'Setup will now exit without making any changes. Repair or remove the invalid file, then run Setup again.',
        mbError, MB_OK)
    else
      MsgBox('An existing Secret Grounds client configuration was found at ' + PreviousInstallLocation + '\client\config.json' +
        ' but it could not be parsed: ' + ExistingConfigError + #13#10#13#10 +
        'Setup could not create a backup of the invalid file. Setup will now exit without making any changes. ' +
        'Repair or remove the invalid file, then run Setup again.',
        mbError, MB_OK);
    Result := False;
  end;
end;

procedure InitializeWizard;
begin
  RolePage := CreateInputOptionPage(
    wpWelcome,
    'Choose Computer Role',
    'Select how this computer will run Secret Grounds.',
    'Install only the files needed for this role.',
    True,
    False
  );
  RolePage.Add('Cashier Computer - controls customer PCs, stores shop data, runs the dashboard.');
  RolePage.Add('Customer Computer - connects to the cashier and receives sessions and commands.');
  RolePage.Values[0] := ExistingConfigStatus <> 'Ok';
  RolePage.Values[1] := ExistingConfigStatus = 'Ok';

  CashierOptionsPage := CreateInputOptionPage(
    RolePage.ID,
    'Cashier Computer Options',
    'Choose startup options.',
    'Secret Grounds can start automatically so the shop opens without extra steps.',
    False,
    False
  );
  CashierOptionsPage.Add('Start Secret Grounds automatically when this computer signs in.');
  CashierOptionsPage.Values[0] := (not ExistingCashierConfigFound) or ExistingCashierAutostartEnabled;

  if ExistingConfigStatus = 'Ok' then
    ClientPage := CreateInputQueryPage(
      RolePage.ID,
      'Customer Client Setup',
      'Existing configuration detected for station ''' + ExistingStationId + '''.',
      'These values were read from the currently installed configuration and will be kept unless you change them.'
    )
  else
    ClientPage := CreateInputQueryPage(
      RolePage.ID,
      'Customer Client Setup',
      'Enter the station and cashier connection details.',
      'Validation runs before installation unless offline preparation is selected.'
    );
  ClientPage.Add('Station ID:', False);
  ClientPage.Add('Display name:', False);
  ClientPage.Add('Cashier server IP or hostname:', False);
  ClientPage.Add('Server port:', False);
  if ExistingConfigStatus = 'Ok' then begin
    ClientPage.Values[0] := ExistingStationId;
    ClientPage.Values[1] := ExistingDisplayName;
    ClientPage.Values[2] := ExistingServerHost;
    ClientPage.Values[3] := ExistingServerPort;
  end else begin
    ClientPage.Values[0] := 'PC-01';
    ClientPage.Values[1] := 'Front Row 01';
    ClientPage.Values[2] := 'cashier-server';
    ClientPage.Values[3] := '18001';
  end;

  ClientOptionsPage := CreateInputOptionPage(
    ClientPage.ID,
    'Customer Client Options',
    'Choose startup and validation options.',
    'Offline preparation saves config without contacting the cashier.',
    False,
    False
  );
  ClientOptionsPage.Add('Start automatically with Windows.');
  ClientOptionsPage.Add('Continue without online validation for offline preparation.');
  ClientOptionsPage.Values[0] := True;
  ClientOptionsPage.Values[1] := False;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = ClientPage.ID) or (PageID = ClientOptionsPage.ID) then
    Result := SelectedRole <> 'client';
  if PageID = CashierOptionsPage.ID then
    Result := SelectedRole <> 'cashier';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Port: Integer;
begin
  Result := True;
  if CurPageID = RolePage.ID then begin
    if RolePage.Values[0] then
      SelectedRole := 'cashier'
    else
      SelectedRole := 'client';
  end;

  if CurPageID = ClientPage.ID then begin
    if Trim(ClientPage.Values[0]) = '' then begin MsgBox('Station ID is required.', mbError, MB_OK); Result := False; Exit; end;
    if Pos(' ', ClientPage.Values[0]) > 0 then begin MsgBox('Station ID may not contain spaces.', mbError, MB_OK); Result := False; Exit; end;
    if CompareText(ClientPage.Values[0], 'CHANGE-ME') = 0 then begin MsgBox('Station ID must be unique for this PC.', mbError, MB_OK); Result := False; Exit; end;
    if Trim(ClientPage.Values[1]) = '' then begin MsgBox('Display name is required.', mbError, MB_OK); Result := False; Exit; end;
    if Trim(ClientPage.Values[2]) = '' then begin MsgBox('Cashier server address is required.', mbError, MB_OK); Result := False; Exit; end;
    Port := StrToIntDef(ClientPage.Values[3], 0);
    if (Port < 1) or (Port > 65535) then begin MsgBox('Server port must be between 1 and 65535.', mbError, MB_OK); Result := False; Exit; end;

    if ExistingConfigStatus = 'Ok' then begin
      if (ClientPage.Values[0] = ExistingStationId) and
         (ClientPage.Values[1] = ExistingDisplayName) and
         (ClientPage.Values[2] = ExistingServerHost) and
         (ClientPage.Values[3] = ExistingServerPort) then
        ConfigModeValue := 'Preserve'
      else
        ConfigModeValue := 'Update';
    end else
      ConfigModeValue := 'Fresh';
  end;

  if CurPageID = ClientOptionsPage.ID then begin
    if ConfigModeValue = 'Preserve' then begin
      Result := True;
      Exit;
    end;
    Port := StrToIntDef(ClientPage.Values[3], 0);
    if Port = 8000 then begin MsgBox('Production clients must not target development port 8000.', mbError, MB_OK); Result := False; Exit; end;
    if ClientOptionsPage.Values[1] then begin
      Result := MsgBox('Offline preparation skips server reachability and station availability checks. The client will not connect until configured correctly. Continue?', mbConfirmation, MB_YESNO) = IDYES;
      Exit;
    end;
    Result := ValidateClientOnline(ClientPage.Values[2], ClientPage.Values[3], ClientPage.Values[0]);
  end;
end;

function CashierServicesAreHealthy: Boolean;
begin
  Result := CashierServicesHealthy;
end;

function RunCashierScript(ScriptName: String; Arguments: String): Boolean;
var
  ResultCode: Integer;
  ScriptPath: String;
begin
  ScriptPath := ExpandConstant('{app}\scripts\' + ScriptName);
  Result := Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -File "' + ScriptPath + '" ' + Arguments,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function CashierLogPathsMessage: String;
begin
  Result := 'Check:' + #13#10 +
    ExpandConstant('{app}\logs\server.log') + #13#10 +
    ExpandConstant('{app}\logs\dashboard.log');
end;

function CashierAutostartArgValue: String;
begin
  // install-cashier-role.ps1 takes a plain presence/absence [switch], not a
  // boolean value: an externally-invoked "powershell.exe -File ... -Param:$true"
  // does not reliably bind boolean-valued parameters across the process
  // boundary (the literal text arrives unparsed and fails type conversion).
  if CashierOptionsPage.Values[0] then
    Result := ''
  else
    Result := '-DisableAutostart';
end;

procedure RunCashierPostInstall;
var
  Retry: Boolean;
begin
  if SelectedRole <> 'cashier' then Exit;

  CashierServicesHealthy := False;

  if not RunCashierScript('initialize-cashier-config.ps1', '-BackendPort 18001 -DashboardPort 3001') then begin
    MsgBox('Secret Grounds could not create the cashier configuration.' + #13#10#13#10 +
      CashierLogPathsMessage, mbError, MB_OK);
    Exit;
  end;

  if not RunCashierScript('install-backend-firewall-rule.ps1', '') then begin
    MsgBox('Secret Grounds could not install the Private-network firewall rule for the backend.' + #13#10#13#10 +
      CashierLogPathsMessage, mbError, MB_OK);
    Exit;
  end;

  if not RunCashierScript('install-cashier-role.ps1', CashierAutostartArgValue) then begin
    MsgBox('Secret Grounds could not configure the cashier startup role.' + #13#10#13#10 +
      CashierLogPathsMessage, mbError, MB_OK);
    Exit;
  end;

  Retry := True;
  while Retry do begin
    Retry := False;
    // -NoBrowser: this runs hidden (SW_HIDE) under the installer's own elevated
    // token, before the wizard has even finished. Launching a browser from that
    // context is pointless (the user cannot see the wizard yet) and risks
    // Start-Process throwing under an elevated/non-interactive shell process,
    // which would tear down an already-healthy backend/dashboard and report a
    // false "backend could not start". The dashboard is opened for real once,
    // on the user's own desktop, by the [Run] postinstall entry below, gated on
    // CashierServicesAreHealthy.
    if RunCashierScript('restart-secret-grounds.ps1', '-NoBrowser') then begin
      CashierServicesHealthy := True;
    end else begin
      if MsgBox(
        'Secret Grounds was installed, but the backend could not start.' + #13#10#13#10 +
        CashierLogPathsMessage + #13#10#13#10 +
        'Would you like to try starting Secret Grounds again?',
        mbError, MB_RETRYCANCEL) = IDRETRY then
        Retry := True;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunCashierPostInstall;
end;

function InitializeUninstall: Boolean;
var
  ResultCode: Integer;
  StoppedCleanly: Boolean;
begin
  // Runs before any uninstall UI appears. Stops the client, backend, and
  // dashboard (whichever this install actually has running) and verifies
  // -- by executable path, not just PID-tracking that can go stale -- that
  // nothing is left running, with a bounded 10-second wait
  // (scripts\stop-all-for-uninstall.ps1). A clean uninstall depends on this:
  // Windows cannot delete a file a running process still has open, which is
  // exactly why {app} used to survive uninstall with the client/dashboard
  // still connected. If processes can't be stopped in time, abort here
  // rather than silently leaving a partially-deleted install directory.
  StoppedCleanly := Exec('powershell.exe',
    '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\stop-all-for-uninstall.ps1') + '"',
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);

  Result := True;
  if not StoppedCleanly then begin
    MsgBox('Secret Grounds could not stop all running processes (client, backend, or dashboard) within 10 seconds.' + #13#10#13#10 +
      'Please close them manually (Task Manager), then run Uninstall again.' + #13#10#13#10 +
      'Check: ' + ExpandConstant('{app}\logs\server.log'),
      mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    RetainShopData := True;
    if MsgBox('Keep Secret Grounds shop data, backups, configuration, and logs? This is recommended. Choose No only if you want permanent deletion.', mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO then
      RetainShopData := False;
  end;

  if (CurUninstallStep = usPostUninstall) and (not RetainShopData) then begin
    DelTree(ExpandConstant('{app}\config'), True, True, True);
    DelTree(ExpandConstant('{app}\data'), True, True, True);
    DelTree(ExpandConstant('{app}\backups'), True, True, True);
    DelTree(ExpandConstant('{app}\logs'), True, True, True);
  end;
end;
