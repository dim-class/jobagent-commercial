from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "packaging" / "jobagent-installer.iss"
BUILD_SCRIPT = REPO / "scripts" / "build-windows-installer.ps1"
LIFECYCLE_SCRIPT = REPO / "scripts" / "test-windows-installer-lifecycle.ps1"
WORKFLOW = REPO / ".github" / "workflows" / "windows-installer.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_installer_is_stable_per_user_and_non_admin() -> None:
    script = _text(INSTALLER)

    assert "AppId={{A97560A6-9D77-4A54-A338-AC2C19D4789D}" in script
    assert "PrivilegesRequired=lowest" in script
    assert "PrivilegesRequiredOverridesAllowed" not in script
    assert r"DefaultDirName={localappdata}\Programs\JobAgent" in script
    assert "ArchitecturesAllowed=x64compatible" in script


def test_installer_payload_is_only_the_audited_bundle() -> None:
    script = _text(INSTALLER)
    files_section = script.split("[Files]", 1)[1].split("[Icons]", 1)[0]

    assert 'Source: "{#BundleDir}\\*"' in files_section
    assert "recursesubdirs" in files_section
    assert "localappdata" not in files_section.lower()
    assert "data\\" not in files_section.lower()


def test_silent_uninstall_always_preserves_user_data() -> None:
    script = _text(INSTALLER)

    assert "if not UninstallSilent then" in script
    assert "DeleteUserData := False;" in script
    assert "MB_DEFBUTTON2" in script
    assert "IDNO" in script
    assert "and DeleteUserData then" in script
    assert "DelTree(JobAgentDataDir, True, True, True);" in script
    assert "[UninstallDelete]" not in script


def test_upgrade_and_uninstall_stop_only_through_jobagent_guard() -> None:
    script = _text(INSTALLER)

    assert "procedure StopInstalledJobAgent;" in script
    assert "Exec(ExecutablePath, '--stop'" in script
    assert "function PrepareToInstall" in script
    assert "CurUninstallStep = usUninstall" in script
    assert "CloseApplications=no" in script
    forbidden = ("taskkill", "stop-process", "wmic", "terminateprocess")
    assert all(token not in script.lower() for token in forbidden)


def test_build_reaudits_payload_and_emits_integrity_metadata() -> None:
    script = _text(BUILD_SCRIPT)

    assert "scripts\\release_audit.py" in script
    assert "Audited portable bundle is missing" in script
    assert "Pinned Inno Setup 7.1.0 is required" in script
    assert "INSTALLER-SHA256SUMS.txt" in script
    assert "INSTALLER-MANIFEST.json" in script
    assert "Get-AuthenticodeSignature" in script
    assert '[string]$RequiredInnoVersion = "7.1.0"' in script
    assert "commercial_distribution_ready = $false" in script
    assert script.index("Programs\\Inno Setup 7\\ISCC.exe") < script.index(
        "Get-Command ISCC.exe"
    )


def test_clean_checkout_workflow_builds_and_uploads_installer() -> None:
    workflow = _text(WORKFLOW)

    assert "runs-on: windows-2025" in workflow
    assert "build-windows-installer.ps1" in workflow
    assert "JRSoftware.InnoSetup.7 --version 7.1.0" in workflow
    assert "JobAgent-Setup-*.exe" in workflow
    assert "INSTALLER-SHA256SUMS.txt" in workflow
    assert "include-hidden-files: true" in workflow
    assert '"$baseVersion-a"' in workflow
    assert '"$baseVersion-b" -SkipPortableBuild' in workflow
    assert "test-windows-installer-lifecycle.ps1" in workflow
    assert workflow.index("test-windows-installer-lifecycle.ps1") < workflow.index(
        "actions/upload-artifact@v4"
    )


def test_lifecycle_gate_is_ephemeral_runner_only_and_refuses_existing_state() -> None:
    script = _text(LIFECYCLE_SCRIPT)

    assert "$env:GITHUB_ACTIONS -ne 'true'" in script
    assert "$env:RUNNER_TEMP" in script
    assert "Refusing pre-existing JobAgent installation" in script
    assert "Refusing pre-existing JobAgent user data" in script
    assert "Refusing pre-existing JobAgent uninstall registration" in script
    assert "Assert-ChildPath $InstallRoot $LocalAppData" in script
    assert "Assert-ChildPath $DataRoot $LocalAppData" in script
    assert "Assert-ChildPath $WorkRoot $env:RUNNER_TEMP" in script


def test_lifecycle_gate_proves_frozen_runtime_upgrade_and_preservation() -> None:
    script = _text(LIFECYCLE_SCRIPT)

    assert "Get-Command python.exe" in script
    assert "Get-Command node.exe" in script
    assert "--doctor" in script
    assert "--no-open" in script
    assert "Wait-Healthy" in script
    assert "health.status -eq 'ok'" in script
    assert "health.database -eq 'ok'" in script
    assert "Get-FileHash -LiteralPath $Database" in script
    assert "installer-lifecycle-sentinel.txt" in script
    assert "Same-AppId upgrade" in script
    assert "Silent uninstall" in script
    assert "Test-PortFree $Port" in script


def test_lifecycle_cleanup_uses_guarded_product_stop_and_exact_runner_targets() -> None:
    script = _text(LIFECYCLE_SCRIPT)
    lowered = script.lower()

    assert "& $Executable --data-dir $DataRoot --stop" in script
    assert "Remove-Item -LiteralPath $InstallRoot -Recurse -Force" in script
    assert "Remove-Item -LiteralPath $DataRoot -Recurse -Force" in script
    assert "Remove-Item -LiteralPath $WorkRoot -Recurse -Force" in script
    assert "remove-item -path" not in lowered
    assert "taskkill" not in lowered
    assert "stop-process" not in lowered
