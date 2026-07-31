param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\client-config-tool.ps1"

function Write-SgStatusLine {
    param([string]$Key, [string]$Value)
    $safeValue = ($Value -replace "[\r\n]", " ")
    Write-Output "$Key=$safeValue"
}

try {
    $status = Get-SgClientConfigStatus -ConfigPath $ConfigPath
    Write-SgStatusLine "STATUS" $status.Status

    if ($status.Status -eq "Ok") {
        Write-SgStatusLine "SERVER_HOST" $status.Config.server_host
        Write-SgStatusLine "SERVER_PORT" $status.Config.server_port
        Write-SgStatusLine "PC_NAME" $status.Config.pc_name
        Write-SgStatusLine "DISPLAY_NAME" $status.Config.display_name
    }

    if ($status.Status -eq "Invalid") {
        Write-SgStatusLine "ERROR" $status.Error
        try {
            $backupDir = Join-Path (Split-Path -Parent $ConfigPath) "config-backups"
            $backupPath = Backup-SgClientConfig -ConfigPath $ConfigPath -BackupDir $backupDir
            Write-SgStatusLine "BACKUP_PATH" $backupPath
        } catch {
            Write-SgStatusLine "BACKUP_ERROR" $_.Exception.Message
        }
    }
} catch {
    Write-SgStatusLine "STATUS" "UNKNOWN"
    Write-SgStatusLine "ERROR" $_.Exception.Message
}
