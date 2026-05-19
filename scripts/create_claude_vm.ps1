#Requires -RunAsAdministrator
#Requires -Version 5.1

# Creates a Hyper-V VM for running Claude Code unsupervised against
# the Claude Code Playspace projects. Run from an elevated PowerShell.

# ============ Config -- tweak before running ============
$VMName         = 'claude-playspace'
$VMRoot         = 'D:\Hyper-V'
$RAMStartup     = 4GB
$RAMMin         = 2GB
$RAMMax         = 16GB
$CPUCount       = 6
$DiskSize       = 100GB
$SwitchName     = 'Default Switch'    # built-in NAT, no extra config needed
$ISOPath        = "$VMRoot\iso\ubuntu-24.04.2-live-server-amd64.iso"
$ISODownloadUrl = 'https://releases.ubuntu.com/24.04.2/ubuntu-24.04.2-live-server-amd64.iso'
# =======================================================

$ErrorActionPreference = 'Stop'

# 1. Verify Hyper-V is enabled -- if not, enable + ask for reboot
$feature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All
if ($feature.State -ne 'Enabled') {
    Write-Host "Hyper-V not enabled -- enabling now (reboot required after)." -ForegroundColor Yellow
    Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -All -NoRestart | Out-Null
    Write-Warning "Reboot Windows, then re-run this script."
    exit 1
}

# 2. Fail early if VM name is taken -- don't clobber an existing one
if (Get-VM -Name $VMName -ErrorAction SilentlyContinue) {
    Write-Error "VM '$VMName' already exists. Delete it (Remove-VM) or change `$VMName."
    exit 1
}

# 3. Prep directories
$VMPath   = Join-Path $VMRoot $VMName
$VHDXPath = Join-Path $VMPath "$VMName.vhdx"
$ISODir   = Split-Path $ISOPath -Parent
New-Item -ItemType Directory -Path $VMPath -Force | Out-Null
New-Item -ItemType Directory -Path $ISODir -Force | Out-Null

# 4. Download Ubuntu ISO if missing (~2.5GB, one-shot)
if (-not (Test-Path $ISOPath)) {
    Write-Host "Downloading Ubuntu 24.04 ISO to $ISOPath ..." -ForegroundColor Cyan
    $ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is 10x slower with progress bar on
    Invoke-WebRequest -Uri $ISODownloadUrl -OutFile $ISOPath -UseBasicParsing
    $ProgressPreference = 'Continue'
}

# 5. Create Gen2 VM -- Gen2 gives UEFI + Secure Boot + larger disk + faster boot
New-VM -Name $VMName `
       -MemoryStartupBytes $RAMStartup `
       -Generation 2 `
       -NewVHDPath $VHDXPath `
       -NewVHDSizeBytes $DiskSize `
       -SwitchName $SwitchName `
       -Path $VMPath | Out-Null

# 6. CPU + dynamic memory (balloon up to $RAMMax, release back to $RAMMin when idle)
Set-VMProcessor -VMName $VMName -Count $CPUCount
Set-VMMemory    -VMName $VMName `
                -DynamicMemoryEnabled $true `
                -MinimumBytes $RAMMin `
                -MaximumBytes $RAMMax `
                -StartupBytes $RAMStartup

# 7. Mount install ISO and set DVD as first boot device
Add-VMDvdDrive  -VMName $VMName -Path $ISOPath
$dvd = Get-VMDvdDrive -VMName $VMName
Set-VMFirmware  -VMName $VMName -FirstBootDevice $dvd

# 8. Ubuntu ships with the MS-UEFI-CA-signed shim, NOT the MS Windows CA template
#    -- without this swap the installer kernel won't boot under Secure Boot.
Set-VMFirmware  -VMName $VMName -SecureBootTemplate 'MicrosoftUEFICertificateAuthority'

# 9. Disable automatic checkpoints (they fire on every start -- noisy for a workhorse VM).
#    We'll take manual checkpoints before risky Claude runs instead.
Set-VM -Name $VMName -AutomaticCheckpointsEnabled $false -CheckpointType Production

# 10. Integration services on (clipboard, time sync, guest shutdown)
Get-VMIntegrationService -VMName $VMName | Enable-VMIntegrationService

Write-Host ""
Write-Host "VM '$VMName' created at $VMPath" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Start-VM -Name $VMName"
Write-Host "  2. vmconnect.exe localhost $VMName    # opens console for Ubuntu installer"
Write-Host "  3. Walk through Ubuntu Server install (enable OpenSSH server when prompted)"
Write-Host "  4. After first boot + login:  Set-VMDvdDrive -VMName $VMName -Path `$null   # unmount ISO"
Write-Host "  5. Checkpoint-VM -Name $VMName -SnapshotName 'fresh-install'   # baseline rollback point"
Write-Host ""
Write-Host "Get guest IP after install:  (Get-VMNetworkAdapter -VMName $VMName).IPAddresses"
