"""Run make_garment.py for a list of garments, one after another, logging everything.

usage: python batch_garments.py <log_file> <id>:<Name>[:<description>] ...
Continues past failures and prints a summary at the end.
"""
import os, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
log_path = sys.argv[1]
jobs = []
for spec in sys.argv[2:]:
    parts = spec.split(":", 2)
    jobs.append((parts[0], parts[1], parts[2] if len(parts) > 2 else f"{parts[1]}, shown at true size."))

results = []
with open(log_path, "a", encoding="utf-8") as log:
    def say(s):
        log.write(s + "\n"); log.flush(); print(s, flush=True)
    for gid, name, desc in jobs:
        t0 = time.time()
        say(f"\n##### garment {gid}: {name}  ({time.strftime('%H:%M:%S')})")
        res = subprocess.run([sys.executable, os.path.join(ROOT, "make_garment.py"), gid, name, desc],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
        log.write((res.stdout or "") + (res.stderr or "")); log.flush()
        ok = res.returncode == 0
        results.append((gid, name, ok, time.time() - t0))
        say(f"##### garment {gid}: {'OK' if ok else 'FAILED (exit %d)' % res.returncode} in {time.time() - t0:.0f}s")
    say("\n===== SUMMARY =====")
    for gid, name, ok, dt in results:
        say(f"{gid:>3}  {'OK    ' if ok else 'FAILED'}  {dt:6.0f}s  {name}")
    say("===== BATCH DONE =====")
