<#
.SYNOPSIS
  Authenticode-sign Windows files with Azure Artifact Signing (signtool + dlib).

.DESCRIPTION
  Usage: pwsh scripts/sign_windows.ps1 <file> [<file> ...]

  Reads these environment variables (set them from GitHub secrets):
    AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
    AZURE_ARTIFACT_SIGNING_ENDPOINT, AZURE_ARTIFACT_SIGNING_ACCOUNT,
    AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE

  None set    -> warns and exits 0 (files stay unsigned; forks and local runs).
  Partial set -> fails, so a half-configured release never ships silently.

  Signs with SHA256 and an RFC 3161 timestamp. The timestamp is required:
  Artifact Signing certificates last about three days, and the timestamp is
  what keeps the signature valid afterwards.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
  [string[]] $Files
)

$ErrorActionPreference = "Stop"

# Keep these in step with scripts/package_msix.py.
$ClientVersion = "1.0.146"
$NugetUrl = "https://dist.nuget.org/win-x86-commandline/v6.13.2/nuget.exe"
$TimestampUrl = "http://timestamp.acs.microsoft.com"
$MinSigntool = [version]"10.0.2261.755"

$required = @(
  "AZURE_TENANT_ID",
  "AZURE_CLIENT_ID",
  "AZURE_CLIENT_SECRET",
  "AZURE_ARTIFACT_SIGNING_ENDPOINT",
  "AZURE_ARTIFACT_SIGNING_ACCOUNT",
  "AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE"
)
$missing = @($required | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })

if ($missing.Count -eq $required.Count) {
  Write-Host "::warning::Azure Artifact Signing secrets are not set. Not signing: $($Files -join ', ')"
  exit 0
}
if ($missing.Count -gt 0) {
  Write-Host "::error::Azure Artifact Signing secrets are incomplete. Missing: $($missing -join ', ')"
  exit 1
}

foreach ($f in $Files) {
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) {
    throw "File to sign not found: $f"
  }
}

# --- signtool (needs a recent Windows SDK) ----------------------------------
$kitRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$signtool = Get-ChildItem -Path $kitRoot -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
  ForEach-Object {
    $v = $null
    if ([version]::TryParse($_.VersionInfo.FileVersion.Split(" ")[0], [ref]$v)) {
      [pscustomobject]@{ Path = $_.FullName; Version = $v }
    }
  } |
  Where-Object { $_.Version -ge $MinSigntool } |
  Sort-Object Version -Descending |
  Select-Object -First 1
if (-not $signtool) {
  throw "signtool.exe $MinSigntool or newer (x64) not found under $kitRoot."
}
Write-Host "signtool: $($signtool.Path) ($($signtool.Version))"

# --- Artifact Signing client dlib -------------------------------------------
$work = Join-Path $env:RUNNER_TEMP "artifact-signing"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$nuget = Join-Path $work "nuget.exe"
if (-not (Test-Path $nuget)) {
  Invoke-WebRequest -Uri $NugetUrl -OutFile $nuget
}
& $nuget install Microsoft.ArtifactSigning.Client -Version $ClientVersion `
  -OutputDirectory $work -NonInteractive -Verbosity quiet
if ($LASTEXITCODE -ne 0) { throw "nuget install failed with exit $LASTEXITCODE" }

$dlib = Join-Path $work "Microsoft.ArtifactSigning.Client.$ClientVersion\bin\x64\Azure.CodeSigning.Dlib.dll"
if (-not (Test-Path $dlib)) {
  $dlib = (Get-ChildItem -Path $work -Filter Azure.CodeSigning.Dlib.dll -Recurse |
    Where-Object { $_.Directory.Name -ieq "x64" } | Select-Object -First 1).FullName
}
if (-not $dlib) { throw "Azure.CodeSigning.Dlib.dll (x64) not found after nuget install." }

# --- metadata.json (the client secret is NOT written here) ------------------
# EnvironmentCredential stays enabled; everything else is excluded so the
# runner never waits on an interactive or managed-identity login.
$metadata = Join-Path $work "metadata.json"
@{
  Endpoint               = $env:AZURE_ARTIFACT_SIGNING_ENDPOINT
  CodeSigningAccountName = $env:AZURE_ARTIFACT_SIGNING_ACCOUNT
  CertificateProfileName = $env:AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE
  ExcludeCredentials     = @(
    "ManagedIdentityCredential",
    "WorkloadIdentityCredential",
    "SharedTokenCacheCredential",
    "VisualStudioCredential",
    "VisualStudioCodeCredential",
    "AzureCliCredential",
    "AzurePowerShellCredential",
    "AzureDeveloperCliCredential",
    "InteractiveBrowserCredential"
  )
} | ConvertTo-Json | Set-Content -Path $metadata -Encoding utf8

# --- sign + verify -----------------------------------------------------------
foreach ($f in $Files) {
  $full = (Resolve-Path -LiteralPath $f).Path
  Write-Host "Signing $full"
  & $signtool.Path sign /v /debug /fd SHA256 /tr $TimestampUrl /td SHA256 `
    /dlib $dlib /dmdf $metadata $full
  if ($LASTEXITCODE -ne 0) { throw "signtool sign failed for $full (exit $LASTEXITCODE)" }

  & $signtool.Path verify /pa /v $full
  if ($LASTEXITCODE -ne 0) { throw "signtool verify failed for $full (exit $LASTEXITCODE)" }
}
Write-Host "Signed $($Files.Count) file(s)."
