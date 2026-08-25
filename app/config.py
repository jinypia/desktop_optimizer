"""Central configuration: sampling cadence, alert thresholds, hysteresis."""
from dataclasses import dataclass

SAMPLE_INTERVAL_S = 1.5      # dashboard on screen
SAMPLE_INTERVAL_MINI_S = 2.5  # mini strip only — still wants to look live
SAMPLE_INTERVAL_BG_S = 4.0   # nothing on screen (tray only / minimised)
HISTORY_SAMPLES = 240        # chart history (~6 minutes at 1.5 s)
TOP_PROCESS_COUNT = 6        # rows in the top-process table

# The all-process scan (top CPU/memory lists) is still the sampler's
# largest single step, though app/procsnap.py cut it from ~38 ms to ~5 ms
# by asking the kernel once instead of per process. Run it every Nth
# cycle: often when someone is watching, rarely when nobody is. There is
# now headroom to lower these if fresher top-process data is wanted.
PROC_SCAN_EVERY = 2
PROC_SCAN_EVERY_BG = 8
VOLUME_SCAN_EVERY = 10       # per-drive disk_usage every Nth cycle
FREQ_SCAN_EVERY = 10         # cpu_freq() every Nth cycle (slow on Windows)

# The app polices its own cost: if it averages more than this share of the
# machine's CPU over ~60 s it raises a warning against itself.
SELF_CPU_BUDGET = 5.0        # % of total machine CPU

# Volume fullness is checked per drive on every sample (no sustain window)
DISK_FULL_WARN = 90          # % used
DISK_FULL_CRITICAL = 97


@dataclass(frozen=True)
class Rule:
    """A sustained-threshold rule with hysteresis.

    The metric must stay above `warn_at`/`critical_at` for `sustain_s`
    seconds to fire, and must drop below `clear_below` to re-arm — so one
    episode of degradation produces one alert, not one per sample.
    """
    rule_id: str
    metric: str
    warn_at: float
    critical_at: float
    clear_below: float
    sustain_s: float
    unit: str = "%"


RULES = (
    Rule("cpu_high",  "cpu",       warn_at=85,  critical_at=96,  clear_below=70, sustain_s=20),
    Rule("mem_high",  "memory",    warn_at=85,  critical_at=93,  clear_below=78, sustain_s=15),
    Rule("disk_busy", "disk_busy", warn_at=90,  critical_at=98,  clear_below=60, sustain_s=30),
    Rule("ui_lag",    "ui_lag",    warn_at=150, critical_at=500, clear_below=80, sustain_s=15, unit=" ms"),
)
