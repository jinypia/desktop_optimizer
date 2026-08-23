"""Measure the app's own CPU/RAM cost from outside the process.

Run while Desktop Optimizer is running (installed build or from source):
    python tests\\measure_overhead.py [seconds]
"""
import sys
import time

import psutil

DEFAULT_SECONDS = 80
STEP = 2.0


def find_app():
    targets = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or []).lower()
            if name.startswith("desktopoptimizer") or (
                    name.startswith("pythonw") and "desktop_optimizer" in cmd):
                targets.append(psutil.Process(p.pid))
        except psutil.Error:
            continue
    return targets


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS
    ncpu = psutil.cpu_count(logical=True) or 1
    procs = find_app()
    if not procs:
        print("NO APP PROCESS FOUND — start Desktop Optimizer first")
        return 1
    print(f"measuring pids {[p.pid for p in procs]} for {seconds}s "
          f"({ncpu} logical CPUs)")
    for p in procs:
        p.cpu_percent(None)

    cpu, rss = [], []
    for _ in range(int(seconds / STEP)):
        time.sleep(STEP)
        c = m = 0.0
        for p in procs:
            try:
                c += p.cpu_percent(None)
                m += p.memory_info().rss
            except psutil.Error:
                continue
        cpu.append(c / ncpu)        # % of whole-machine capacity
        rss.append(m)

    print(f"RESULT: avg {sum(cpu) / len(cpu):.2f}% CPU | "
          f"peak {max(cpu):.2f}% CPU | "
          f"RAM avg {sum(rss) / len(rss) / 1048576:.0f} MB, "
          f"peak {max(rss) / 1048576:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
