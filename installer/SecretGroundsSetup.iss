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
Source: "..\scripts\run-client-supervised.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\stop-client.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\configure-client.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\client-config-tool.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\install-client-role.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\install-client-autostart.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\remove-client-autostart.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\start-client-after-install.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\install-startup-folder-shortcuts.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\remove-client-startup-folder-shortcut.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Check: IsClient
Source: "..\scripts\client-config-tool.ps1"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\scripts\read-client-config-status.ps1"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\scripts\read-cashier-status.ps1"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{group}\Secret Grounds Dashboard"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\open-dashboard.ps1"""; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 14; Check: IsCashier
Name: "{group}\Start Secret Grounds"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\start-secret-grounds.ps1"""; WorkingDir: "{app}"; Check: IsCashier
Name: "{group}\Stop Secret Grounds"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\stop-secret-grounds.ps1"""; WorkingDir: "{app}"; Check: IsCashier
Name: "{group}\Restart Secret Grounds"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\restart-secret-grounds.ps1"""; WorkingDir: "{app}"; Check: IsCashier
Name: "{group}\Secret Grounds Status"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\status-secret-grounds.ps1"""; WorkingDir: "{app}"; Check: IsCashier
Name: "{commondesktop}\Secret Grounds Dashboard"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\open-dashboard.ps1"""; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 14; Check: IsCashier
Name: "{group}\Configure Secret Grounds Client"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\configure-client.ps1"""; WorkingDir: "{app}"; Check: IsClient

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\configure-client.ps1"" -Mode Preserve -ClientConfigPath ""{app}\client\config.json"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Preserving existing client configuration..."; Check: IsClientPreserve
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\configure-client.ps1"" -Mode {code:GetConfigMode} -ServerHost ""{code:GetClientServerHost}"" -ServerPort {code:GetClientServerPort} -StationId ""{code:GetClientStationId}"" -DisplayName ""{code:GetClientDisplayName}""{code:GetDisklessSwitchArg}"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Saving validated client configuration..."; Check: IsClientOnline
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\configure-client.ps1"" -Mode {code:GetConfigMode} -ServerHost ""{code:GetClientServerHost}"" -ServerPort {code:GetClientServerPort} -StationId ""{code:GetClientStationId}"" -DisplayName ""{code:GetClientDisplayName}"" -AllowOffline{code:GetDisklessSwitchArg}"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Saving offline client configuration..."; Check: IsClientOffline
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\install-client-autostart.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; StatusMsg: "Installing client autostart..."; Check: ShouldInstallClientAutostart
Filename: "http://127.0.0.1:3001"; Description: "Open Secret Grounds Dashboard"; Flags: postinstall shellexec skipifsilent; Check: CashierServicesAreHealthy

[UninstallRun]
; Belt-and-suspenders re-run of the same stop-everything check that
; InitializeUninstall already gated on: the "Keep shop data?" confirmation
; dialog between InitializeUninstall and here can sit open indefinitely, long
; enough in principle for a Task Scheduler trigger to relaunch something.
; Removing the scheduled tasks right after this closes that window for good.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\stop-all-for-uninstall.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\remove-cashier-autostart.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\remove-client-autostart.ps1"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated skipifdoesntexist

[Code]
var
  RolePage: TInputOptionWizardPage;
  ClientModePage: TInputOptionWizardPage;
  ClientPage: TInputQueryWizardPage;
  ClientDisklessPage: TInputQueryWizardPage;
  ClientOptionsPage: TInputOptionWizardPage;
  CashierOptionsPage: TInputOptionWizardPage;
  SelectedRole: String;
  SelectedClientMode: String;
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

function IsDisklessClient: Boolean;
begin
  Result := (SelectedRole = 'client') and
    ((SelectedClientMode = 'diskless') or (SelectedClientMode = 'diskless_hostname'));
end;

function IsHostnameAdoptClient: Boolean;
begin
  Result := (SelectedRole = 'client') and (SelectedClientMode = 'diskless_hostname');
end;

function IsAutomaticStationId(Value: String): Boolean;
begin
  // Both diskless variants store a non-literal pc_name: '' (dashboard MAC
  // assignment) or '@hostname' (adopt the seat's Windows computer name).
  Result := (Trim(Value) = '') or (Trim(Value) = '@hostname');
end;

function ExpectedDisklessSentinel: String;
begin
  // The pc_name that configure-client.ps1 will write for the currently
  // selected diskless variant.
  if SelectedClientMode = 'diskless_hostname' then
    Result := '@hostname'
  else
    Result := '';
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
  if IsDisklessClient then
    Result := ClientDisklessPage.Values[0]
  else
    Result := ClientPage.Values[1];
end;

function GetClientServerPort(Value: String): String;
begin
  // The cashier backend always listens on 18001 -- the installer no
  // longer asks for it.
  Result := '18001';
end;

function GetClientStationId(Value: String): String;
begin
  // A diskless master image stores no Station ID -- configure-client.ps1 is
  // called with -Diskless and writes "pc_name": "" so every cloned PC
  // resolves its own identity from its wired NIC MAC.
  if IsDisklessClient then
    Result := ''
  else
    Result := ClientPage.Values[0];
end;

function GetClientDisplayName(Value: String): String;
begin
  // No separate display-name field anymore -- the Station ID is the
  // display name.
  if IsDisklessClient then
    Result := ''
  else
    Result := ClientPage.Values[0];
end;

function GetDisklessSwitchArg(Value: String): String;
begin
  if IsHostnameAdoptClient then
    Result := ' -Diskless -AdoptHostname'
  else if IsDisklessClient then
    Result := ' -Diskless'
  else
    Result := '';
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

function ValidateServerReachableAndVersioned(ServerHost: String; ServerPort: String): Boolean;
var
  Http: Variant;
  HealthUrl: String;
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
    Result := True;
  except
    MsgBox('Could not reach ' + HealthUrl + '. Check the cashier address, port, and Private-network firewall rule, or select offline preparation.', mbError, MB_OK);
  end;
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
  CmdParams := '/C powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + ScriptPath +
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
  CmdParams := '/C powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + ScriptPath +
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

  // An existing "Ok" client config with a non-literal Station ID is an
  // already-prepared diskless master image; '@hostname' is the adopt-the-
  // computer-name variant. Otherwise default to a fixed, individually
  // installed station.
  if (ExistingConfigStatus = 'Ok') and (Trim(ExistingStationId) = '@hostname') then
    SelectedClientMode := 'diskless_hostname'
  else if (ExistingConfigStatus = 'Ok') and (Trim(ExistingStationId) = '') then
    SelectedClientMode := 'diskless'
  else
    SelectedClientMode := 'fixed';

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

  // Wizard pages are built as a single deterministic chain, each anchored to
  // the ID of the page immediately before it: RolePage -> ClientModePage ->
  // CashierOptionsPage -> ClientPage -> ClientDisklessPage -> ClientOptionsPage.
  // Do NOT anchor several pages to the same AfterID -- CreateInput*Page inserts
  // at index(AfterID)+1, so shared anchors stack in reverse creation order and
  // would push ClientModePage to the end of the wizard.
  ClientModePage := CreateInputOptionPage(
    RolePage.ID,
    'Customer Computer Setup',
    'Choose how this customer installation is identified.',
    'A Fixed Station stores a permanent Station ID. A Diskless / Automatic image stores no Station ID -- each gaming PC cloned from it is assigned from the dashboard on first boot.',
    True,
    False
  );
  ClientModePage.Add('Fixed Station - a normal, individually installed gaming PC with a permanent Station ID.');
  ClientModePage.Add('Diskless / Automatic - shared/master image; each cloned PC is assigned once from the dashboard.');
  ClientModePage.Add('Diskless / Adopt PC name - shared/master image; each cloned PC adopts the name the diskless system gives it, no dashboard step.');
  ClientModePage.Values[0] := SelectedClientMode = 'fixed';
  ClientModePage.Values[1] := SelectedClientMode = 'diskless';
  ClientModePage.Values[2] := SelectedClientMode = 'diskless_hostname';

  CashierOptionsPage := CreateInputOptionPage(
    ClientModePage.ID,
    'Cashier Computer Options',
    'Choose startup options.',
    'Secret Grounds can start automatically so the shop opens without extra steps.',
    False,
    False
  );
  CashierOptionsPage.Add('Start Secret Grounds automatically when this computer starts.');
  CashierOptionsPage.Values[0] := (not ExistingCashierConfigFound) or ExistingCashierAutostartEnabled;

  if ExistingConfigStatus = 'Ok' then
    ClientPage := CreateInputQueryPage(
      CashierOptionsPage.ID,
      'Customer Client Setup',
      'Existing configuration detected for station ''' + ExistingStationId + '''.',
      'These values were read from the currently installed configuration and will be kept unless you change them.'
    )
  else
    ClientPage := CreateInputQueryPage(
      CashierOptionsPage.ID,
      'Customer Client Setup',
      'Enter the station and cashier connection details.',
      'Validation runs before installation unless offline preparation is selected.'
    );
  ClientPage.Add('Station ID:', False);
  ClientPage.Add('Cashier server IP or hostname:', False);
  if ExistingConfigStatus = 'Ok' then begin
    ClientPage.Values[0] := ExistingStationId;
    ClientPage.Values[1] := ExistingServerHost;
  end else begin
    ClientPage.Values[0] := 'PC-01';
    ClientPage.Values[1] := 'cashier-server';
  end;

  if (ExistingConfigStatus = 'Ok') and (Trim(ExistingStationId) = '') then
    ClientDisklessPage := CreateInputQueryPage(
      ClientPage.ID,
      'Diskless / Automatic Image Setup',
      'Existing diskless image configuration detected.',
      'These values were read from the currently installed configuration and will be kept unless you change them. No Station ID is stored.'
    )
  else
    ClientDisklessPage := CreateInputQueryPage(
      ClientPage.ID,
      'Diskless / Automatic Image Setup',
      'Enter the cashier connection details for this master image.',
      'No Station ID is stored. Every gaming PC cloned from this image is assigned automatically from the dashboard on first boot.'
    );
  ClientDisklessPage.Add('Cashier server IP or hostname:', False);
  if (ExistingConfigStatus = 'Ok') and (Trim(ExistingStationId) = '') then
    ClientDisklessPage.Values[0] := ExistingServerHost
  else
    ClientDisklessPage.Values[0] := 'cashier-server';

  ClientOptionsPage := CreateInputOptionPage(
    ClientDisklessPage.ID,
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
  if (PageID = ClientModePage.ID) or (PageID = ClientOptionsPage.ID) then
    Result := SelectedRole <> 'client';
  if PageID = ClientPage.ID then
    Result := (SelectedRole <> 'client') or (SelectedClientMode <> 'fixed');
  if PageID = ClientDisklessPage.ID then
    Result := not IsDisklessClient;
  if PageID = CashierOptionsPage.ID then
    Result := SelectedRole <> 'cashier';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = RolePage.ID then begin
    if RolePage.Values[0] then
      SelectedRole := 'cashier'
    else
      SelectedRole := 'client';
  end;

  if CurPageID = ClientModePage.ID then begin
    if ClientModePage.Values[2] then
      SelectedClientMode := 'diskless_hostname'
    else if ClientModePage.Values[1] then
      SelectedClientMode := 'diskless'
    else
      SelectedClientMode := 'fixed';
  end;

  if CurPageID = ClientDisklessPage.ID then begin
    if Trim(ClientDisklessPage.Values[0]) = '' then begin MsgBox('Cashier server address is required.', mbError, MB_OK); Result := False; Exit; end;

    // Preserve only when the existing config already matches the chosen
    // diskless variant's pc_name sentinel ('' for dashboard-assignment,
    // '@hostname' for adopt-computer-name) and the server host is unchanged.
    if (ExistingConfigStatus = 'Ok') and
       (Trim(ExistingStationId) = ExpectedDisklessSentinel) and
       (ClientDisklessPage.Values[0] = ExistingServerHost) then
      ConfigModeValue := 'Preserve'
    else if ExistingConfigStatus = 'Ok' then
      ConfigModeValue := 'Update'
    else
      ConfigModeValue := 'Fresh';
  end;

  if CurPageID = ClientPage.ID then begin
    if Trim(ClientPage.Values[0]) = '' then begin MsgBox('Station ID is required.', mbError, MB_OK); Result := False; Exit; end;
    if Pos(' ', ClientPage.Values[0]) > 0 then begin MsgBox('Station ID may not contain spaces.', mbError, MB_OK); Result := False; Exit; end;
    if CompareText(ClientPage.Values[0], 'CHANGE-ME') = 0 then begin MsgBox('Station ID must be unique for this PC.', mbError, MB_OK); Result := False; Exit; end;
    if Trim(ClientPage.Values[1]) = '' then begin MsgBox('Cashier server address is required.', mbError, MB_OK); Result := False; Exit; end;

    if ExistingConfigStatus = 'Ok' then begin
      if (ClientPage.Values[0] = ExistingStationId) and
         (ClientPage.Values[1] = ExistingServerHost) then
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

    if IsDisklessClient then begin
      if ClientOptionsPage.Values[1] then begin
        Result := MsgBox('Offline preparation skips the cashier server reachability check. The diskless image will still be assigned automatically once the gaming PCs can reach the cashier. Continue?', mbConfirmation, MB_YESNO) = IDYES;
        Exit;
      end;
      Result := ValidateServerReachableAndVersioned(ClientDisklessPage.Values[0], '18001');
      Exit;
    end;

    if ClientOptionsPage.Values[1] then begin
      Result := MsgBox('Offline preparation skips server reachability and station availability checks. The client will not connect until configured correctly. Continue?', mbConfirmation, MB_YESNO) = IDYES;
      Exit;
    end;
    Result := ValidateClientOnline(ClientPage.Values[1], '18001', ClientPage.Values[0]);
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
    '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + ScriptPath + '" ' + Arguments,
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

function ClientLogPathMessage: String;
begin
  Result := 'Check: ' + ExpandConstant('{app}\logs\client.log');
end;

function ClientAutostartKeepArgValue: String;
begin
  // Same reasoning as CashierAutostartArgValue: a plain presence/absence
  // [switch], not a boolean value, since an externally-invoked
  // "powershell.exe -File ... -Param:$true" does not reliably bind
  // boolean-valued parameters across the process boundary.
  if ClientOptionsPage.Values[0] then
    Result := '-KeepAutostart'
  else
    Result := '';
end;

procedure RunClientPostInstall;
begin
  if SelectedRole <> 'client' then Exit;

  if IsDisklessClient then begin
    // This is a master image, not a live station. Auto-start is already
    // registered by the checkbox-gated [Run] entry (so every cloned PC
    // launches the client on boot), but the client must not run here --
    // each clone resolves its own identity on first boot: from its wired
    // NIC MAC (assigned from the dashboard), or from its Windows computer
    // name (adopt-PC-name variant).
    if IsHostnameAdoptClient then
      MsgBox('Diskless image ready' + #13#10#13#10 +
        'This computer has been configured to adopt the PC name the diskless ' +
        'system assigns each seat. Create the matching stations (PC-01, PC-02, ...) ' +
        'under Setup / Layout on the dashboard, then capture/clone this image to ' +
        'the gaming PCs. Each seat binds itself on first boot -- no per-PC step.',
        mbInformation, MB_OK)
    else
      MsgBox('Diskless image ready' + #13#10#13#10 +
        'This computer has been configured for automatic station assignment. ' +
        'You can now capture/clone this installation to the gaming PCs. ' +
        'Do not assign a Station ID to this image.',
        mbInformation, MB_OK);
    Exit;
  end;

  // Offline preparation deliberately saves config without contacting the
  // cashier -- starting the client now would immediately try to connect,
  // defeating the entire point of preparing a station offline.
  if not (IsClientOnline or IsClientPreserve) then Exit;

  if not RunCashierScript('start-client-after-install.ps1', ClientAutostartKeepArgValue) then begin
    MsgBox('Secret Grounds was installed, but the client could not be started automatically.' + #13#10#13#10 +
      ClientLogPathMessage + #13#10#13#10 +
      'You can start it later from the Start Menu, or by signing in again if automatic startup is enabled.',
      mbError, MB_OK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    RunCashierPostInstall;
    RunClientPostInstall;
  end;
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
    '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + ExpandConstant('{app}\scripts\stop-all-for-uninstall.ps1') + '"',
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
