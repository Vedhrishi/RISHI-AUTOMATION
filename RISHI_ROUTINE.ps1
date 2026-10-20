# RISHI routine: one guided command for the steps that need a human at the keyboard.
#   powershell -ExecutionPolicy Bypass -File C:\Users\vedhr\RISHI_AUTOMATION\RISHI_ROUTINE.ps1
# This script does not install or schedule anything. Every step that changes a live account
# shows a DRY RUN first and asks y/N; pressing Enter at every prompt changes nothing.

$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$Engine = 'R:\INSTA\sebi_fin_engine'
$Tools  = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'session_scripts'
$Web    = 'R:\INSTA\website'

function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Ask($q)  { (Read-Host "$q [y/N]") -match '^(y|yes)$' }

Step '1/5  Status (read-only)'
python (Join-Path $Tools 'rishi_status.py') --days 10

Step '2/5  Facebook: schedule the remaining Reels as far ahead as Meta allows'
Push-Location $Engine
python fb_topup.py --dry-run
if (Ask 'Schedule these Facebook Reels for real?') { python fb_topup.py }
Pop-Location

Step '3/5  YouTube: realign publish times with the calendar (fixes double-booked slots)'
python (Join-Path $Tools 'fix_youtube_schedule.py')
if (Ask 'Apply these YouTube rescheduling changes?') { python (Join-Path $Tools 'fix_youtube_schedule.py') --apply }

Step '4/5  Website: rebuild data, then deploy to production'
Push-Location $Web
python tools\build_data.py
git status --short | Select-String -NotMatch '^\?\?'
if (Ask 'Deploy the website folder to production now (npx vercel --prod)?') { npx --yes vercel --prod }
Pop-Location

Step '5/5  Status again, to confirm'
python (Join-Path $Tools 'rishi_status.py') --days 10
Write-Host "`nDone. Anything marked WARN above still needs attention." -ForegroundColor Green
