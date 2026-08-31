#ifndef AppVersion
  #error AppVersion must be supplied by the build script
#endif
#ifndef BundleDir
  #error BundleDir must be supplied by the build script
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by the build script
#endif

[Setup]
AppId={{A97560A6-9D77-4A54-A338-AC2C19D4789D}
AppName=JobAgent
AppVersion={#AppVersion}
AppVerName=JobAgent {#AppVersion}
AppPublisher=JobAgent
AppComments=本地优先的求职 Agent
DefaultDirName={localappdata}\Programs\JobAgent
DefaultGroupName=JobAgent
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=JobAgent-Setup-{#AppVersion}-Windows-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
UninstallDisplayName=JobAgent
UninstallDisplayIcon={app}\JobAgent.exe
CloseApplications=no
RestartApplications=no
UsePreviousAppDir=yes
UsePreviousGroup=yes
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimp.DesktopShortcut=创建桌面快捷方式
english.DesktopShortcut=Create a desktop shortcut
chinesesimp.LaunchJobAgent=启动 JobAgent
english.LaunchJobAgent=Launch JobAgent
chinesesimp.DeleteDataTitle=是否删除个人数据？
english.DeleteDataTitle=Delete personal data?
chinesesimp.DeleteDataPrompt=默认会保留简历、岗位、策略、数据库和本地设置。%n%n只有确定不再需要这些数据时才选择“是”。删除后无法通过卸载器恢复。%n%n数据目录：%1
english.DeleteDataPrompt=Your resumes, jobs, strategy, database and local settings are preserved by default.%n%nChoose Yes only if you are certain you no longer need them. The uninstaller cannot restore deleted data.%n%nData directory: %1

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\JobAgent"; Filename: "{app}\JobAgent.exe"; WorkingDir: "{app}"
Name: "{group}\卸载 JobAgent"; Filename: "{uninstallexe}"
Name: "{autodesktop}\JobAgent"; Filename: "{app}\JobAgent.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\JobAgent.exe"; Description: "{cm:LaunchJobAgent}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteUserData: Boolean;

function JobAgentDataDir: String;
begin
  Result := ExpandConstant('{localappdata}\JobAgent');
end;

procedure StopInstalledJobAgent;
var
  ResultCode: Integer;
  ExecutablePath: String;
begin
  ExecutablePath := ExpandConstant('{app}\JobAgent.exe');
  if FileExists(ExecutablePath) then
    Exec(ExecutablePath, '--stop', ExpandConstant('{app}'), SW_HIDE,
      ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopInstalledJobAgent;
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopInstalledJobAgent;
    DeleteUserData := False;
    if not UninstallSilent then
      DeleteUserData :=
        SuppressibleMsgBox(
          FmtMessage(CustomMessage('DeleteDataPrompt'), [JobAgentDataDir]),
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
  end
  else if (CurUninstallStep = usPostUninstall) and DeleteUserData then
    DelTree(JobAgentDataDir, True, True, True);
end;
