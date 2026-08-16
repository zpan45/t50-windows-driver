$ErrorActionPreference = 'Stop'
$Url = 'http://127.0.0.1:8631/ipp/print'
$Name = 'T50 Label'
$Vendor = 'Supvan_T50_Printer'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Re-launching as Administrator...'
    $script = $MyInvocation.MyCommand.Path
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
    exit
}

Write-Host "Waiting for IPP server at $Url ..."
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    throw "IPP server is not running. Start it first: python -m t50 serve"
}

if (Get-Printer -Name $Name -ErrorAction SilentlyContinue) {
    Write-Host "Removing existing printer $Name"
    Remove-Printer -Name $Name
}

Write-Host "Adding $Name via Microsoft IPP Class Driver"
Add-Printer -Name $Name -IppURL $Url

Stop-Service -Name 'Supvan_T50_Service' -ErrorAction SilentlyContinue
Set-Service -Name 'Supvan_T50_Service' -StartupType Manual -ErrorAction SilentlyContinue

$vendorPrinter = Get-CimInstance -ClassName Win32_Printer -Filter "Name='$Vendor'" -ErrorAction SilentlyContinue
if ($vendorPrinter) {
    $vendorPrinter | Set-CimInstance -Property @{ WorkOffline = $true }
    Set-Printer -Name $Vendor -Comment 'Disabled vendor driver — use T50 Label'
    Write-Host "Set $Vendor offline"
}

Write-Host ""
Write-Host "Installed:"
Get-Printer -Name $Name | Format-List Name, DriverName, PortName, PrinterStatus
Write-Host "Print from Word/Chrome to '$Name'. Keep the T50 Label window running (pythonw -m t50)."
