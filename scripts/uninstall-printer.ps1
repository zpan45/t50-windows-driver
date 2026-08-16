$ErrorActionPreference = 'Continue'
$Name = 'T50 Label'
$Vendor = 'Supvan_T50_Printer'

if (Get-Printer -Name $Name -ErrorAction SilentlyContinue) {
    Remove-Printer -Name $Name
    Write-Host "Removed $Name"
} else {
    Write-Host "$Name was not installed"
}

$vendorPrinter = Get-CimInstance -ClassName Win32_Printer -Filter "Name='$Vendor'" -ErrorAction SilentlyContinue
if ($vendorPrinter) {
    $vendorPrinter | Set-CimInstance -Property @{ WorkOffline = $false }
    Set-Printer -Name $Vendor -Comment ''
    Write-Host "Re-enabled $Vendor"
}

Set-Service -Name 'Supvan_T50_Service' -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name 'Supvan_T50_Service' -ErrorAction SilentlyContinue
Write-Host "Vendor service restored (if present)."
