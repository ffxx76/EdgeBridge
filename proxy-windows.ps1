param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 65535)]
    [int]$ProxyPort,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-zA-Z0-9_][a-zA-Z0-9_.@-]*$')]
    [string]$Target,

    [ValidateRange(1024, 65535)]
    [int]$RemotePort = 17890
)

$ErrorActionPreference = 'Stop'
try {
    $ssh = Get-Command ssh.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $curl = Get-Command curl.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
} catch {
    Write-Error 'Windows OpenSSH Client and curl.exe are required.'
    exit 1
}

$localProxy = "http://127.0.0.1:$ProxyPort"
Write-Host "Checking local proxy $localProxy ..."
& $curl.Source --silent --show-error --fail --location --connect-timeout 8 --max-time 20 --noproxy=- --proxy $localProxy --output NUL https://example.com/
if ($LASTEXITCODE -ne 0) {
    Write-Error "Proxy check failed. Confirm that the Windows HTTP/Mixed proxy is running on port $ProxyPort."
    exit 1
}

$remoteProxy = "http://127.0.0.1:$RemotePort"
$remoteCommand = "export http_proxy=$remoteProxy https_proxy=$remoteProxy HTTP_PROXY=$remoteProxy HTTPS_PROXY=$remoteProxy ALL_PROXY=$remoteProxy all_proxy=$remoteProxy; export no_proxy=localhost,127.0.0.1,::1 NO_PROXY=localhost,127.0.0.1,::1; printf 'Checking proxy on the device...\n'; curl --silent --show-error --fail --location --connect-timeout 8 --max-time 20 --noproxy=- --output /dev/null https://example.com/ || { echo 'Device proxy check failed.' >&2; exit 1; }; echo 'Proxy is ready in this shell. Type exit to disconnect.'; exec bash -i"

Write-Host "Connecting to $Target. Authenticate and verify the host key if prompted."
Write-Host 'Keep this terminal and the Windows proxy running while using the connection.'
& $ssh.Source -tt -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -R "127.0.0.1:$($RemotePort):127.0.0.1:$ProxyPort" -- $Target $remoteCommand
exit $LASTEXITCODE
