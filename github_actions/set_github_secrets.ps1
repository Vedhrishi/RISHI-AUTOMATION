# Sets every GitHub Actions secret the rishi-publish workflow needs, straight from your
# local .env and credential files. Run this yourself - it never prints a secret value,
# and nothing here is something Claude can see or run for you.
#
# Prereqs: `gh auth status` already logged in (it was, earlier this session).
# Usage:   powershell -ExecutionPolicy Bypass -File set_github_secrets.ps1

$ErrorActionPreference = 'Stop'
$Repo = 'Vedhrishi/rishi-indian-stocks-explained'
$EnvFile = 'R:\INSTA\sebi_fin_engine\.env'
$Creds   = 'R:\INSTA\sebi_fin_engine\credentials'

function Get-EnvValue($key) {
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
    if (-not $line) { throw "Key '$key' not found in $EnvFile" }
    return $line.Substring($key.Length + 1)
}

function Set-Secret($name, $value) {
    $value | gh secret set $name --repo $Repo
    Write-Host "  set $name" -ForegroundColor Green
}

Write-Host "Setting plain secrets from .env..." -ForegroundColor Cyan
foreach ($key in @('META_ACCESS_TOKEN','FB_APP_ID','FB_APP_SECRET','FB_PAGE_ID',
                   'FB_PAGE_ACCESS_TOKEN','IG_USER_ID','SUPABASE_URL','SUPABASE_SERVICE_KEY')) {
    Set-Secret $key (Get-EnvValue $key)
}

Write-Host "Setting YouTube credential files..." -ForegroundColor Cyan
Get-Content (Join-Path $Creds 'yt_token.json') -Raw | gh secret set YT_TOKEN_JSON --repo $Repo
Write-Host "  set YT_TOKEN_JSON" -ForegroundColor Green
Get-Content (Join-Path $Creds 'client_secret.json') -Raw | gh secret set YT_CLIENT_SECRET_JSON --repo $Repo
Write-Host "  set YT_CLIENT_SECRET_JSON" -ForegroundColor Green

Write-Host "`nDone. Verify the list (names only, GitHub never shows values):" -ForegroundColor Cyan
gh secret list --repo $Repo
