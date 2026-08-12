[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$HostAddress,

    [ValidateRange(1, 65535)]
    [int]$Port = 22,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$User,

    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ComposeFile,

    [ValidateScript({ -not $_ -or (Test-Path -LiteralPath $_ -PathType Leaf) })]
    [string]$IdentityFile = ''
)

$ErrorActionPreference = 'Stop'

foreach ($program in @('ssh', 'scp')) {
    if (-not (Get-Command $program -ErrorAction SilentlyContinue)) {
        throw "$program was not found. Install Windows OpenSSH Client."
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$installScript = Join-Path $scriptRoot 'install-node.sh'
$statusScript = Join-Path $scriptRoot 'node-status.sh'
$uninstallScript = Join-Path $scriptRoot 'uninstall-node.sh'

foreach ($path in @($installScript, $statusScript, $uninstallScript)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required file was not found: $path"
    }
}

$target = "${User}@${HostAddress}"
$sshOptions = @('-p', $Port, '-o', 'ConnectTimeout=15')
$scpOptions = @('-P', $Port, '-o', 'ConnectTimeout=15')
if ($IdentityFile) {
    $resolvedIdentity = (Resolve-Path -LiteralPath $IdentityFile).Path
    $sshOptions += @('-i', $resolvedIdentity)
    $scpOptions += @('-i', $resolvedIdentity)
}

$remoteTemp = "/tmp/remnanode-install-$([Guid]::NewGuid().ToString('N'))"
$remoteFiles = "${target}:${remoteTemp}/"

Write-Host "Testing SSH connection to $target`:$Port ..."
& ssh @sshOptions $target 'uname -s && uname -m'
if ($LASTEXITCODE -ne 0) { throw 'SSH connection failed.' }

try {
    & ssh @sshOptions $target "mkdir -p '$remoteTemp' && chmod 700 '$remoteTemp'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the remote temporary directory.' }

    & scp @scpOptions -- $ComposeFile $installScript $statusScript $uninstallScript $remoteFiles
    if ($LASTEXITCODE -ne 0) { throw 'Could not copy files to the server.' }

    $remoteCommand = "chmod 700 '$remoteTemp/install-node.sh' '$remoteTemp/node-status.sh' '$remoteTemp/uninstall-node.sh' && sudo '$remoteTemp/install-node.sh' --compose '$remoteTemp/$(Split-Path -Leaf $ComposeFile)'"
    & ssh @sshOptions -t $target $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw 'Remnawave Node deployment failed.' }
}
finally {
    & ssh @sshOptions $target "rm -rf '$remoteTemp'" | Out-Null
}

Write-Host 'Done. Check the node status in Remnawave.' -ForegroundColor Green
