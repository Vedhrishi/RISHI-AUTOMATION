"""RISHI alert: run the full status check, and if anything needs attention, make it
hard to miss instead of sitting silently in a log.

- Writes/updates a flag file on the Desktop (RISHI_NEEDS_ATTENTION.txt) only when there's
  a real problem; deletes it when everything's clean, so an old warning never lingers.
- Best-effort also pops a Windows message box (msg.exe) when a session is logged in -
  harmless no-op if nobody's logged in (which is exactly when the file matters more).

Meant to run right after the daily tasks, not instead of them. Install next to
master_orchestrator.py (same folder as rishi_status.py) and schedule a few minutes after
each of RISHI_Morning_Drop_Part1 (06:50), RISHI_Evening_Drop_Part2 (18:50) and
RISHI_FB_Topup_Daily (13:00) - see the Register-ScheduledTask command this script prints
with --print-schedule-command.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FLAG_FILE = Path.home() / "Desktop" / "RISHI_NEEDS_ATTENTION.txt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-schedule-command", action="store_true",
                     help="print the PowerShell command to register this as a task, then exit")
    args = ap.parse_args()

    if args.print_schedule_command:
        print(f'''$action = New-ScheduledTaskAction -Execute "C:\\Program Files\\Python311\\python.exe" -Argument '"{HERE / "rishi_alert.py"}"' -WorkingDirectory "{HERE}"
$t1 = New-ScheduledTaskTrigger -Daily -At 7:10AM
$t2 = New-ScheduledTaskTrigger -Daily -At 7:10PM
$t3 = New-ScheduledTaskTrigger -Daily -At 1:10PM
$principal = New-ScheduledTaskPrincipal -UserId 'vedhr' -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "RISHI_Alert" -Action $action -Trigger @($t1,$t2,$t3) -Principal $principal -Description "Surfaces a Desktop warning file if the daily RISHI tasks hit a real problem" -Force''')
        return 0

    import rishi_status as rs  # noqa: E402  (same folder; reuses every existing check)
    code = rs.main()
    problems = list(rs.problems)

    if not problems:
        if FLAG_FILE.exists():
            FLAG_FILE.unlink()
            print(f"All clear - removed stale {FLAG_FILE}")
        else:
            print("All clear.")
        return 0

    FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"RISHI automation needs attention - checked {datetime.now():%Y-%m-%d %H:%M}\r\n\r\n"
        + "\r\n".join(f"- {p}" for p in problems)
        + f"\r\n\r\nFull report: python \"{HERE / 'rishi_status.py'}\"\r\n"
        + "This file disappears on its own once everything's fixed.\r\n"
    )
    FLAG_FILE.write_text(body, encoding="utf-8")
    print(f"Wrote {FLAG_FILE} ({len(problems)} item(s))")

    try:  # best-effort only - silently does nothing if no one is logged in
        subprocess.run(
            ["msg", "*", f"RISHI needs attention ({len(problems)} item(s)). See {FLAG_FILE}"],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

    return code


if __name__ == "__main__":
    sys.exit(main())
