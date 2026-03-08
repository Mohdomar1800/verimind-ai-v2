from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULES = [
    "ibex_core",
    "otbn",
    "uart",
    "spi_host",
    "gpio",
    "dma",
    "aes",
    "hmac",
    "usbdev",
    "keymgr",
]

FAIL_KINDS = [
    "assertion fail",
    "timeout error",
    "fatal bus error",
    "scoreboard mismatch",
    "crc error",
]

PASS_KINDS = [
    "transaction complete",
    "check passed",
    "interrupt handled",
    "coverage sample recorded",
    "self-check pass",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a sample RTL simulation log file")
    parser.add_argument("--lines", type=int, default=1000, help="Number of log lines to generate")
    parser.add_argument(
        "--output",
        default="sample files/upload_1000_logs.log",
        help="Output log path",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible log generation")
    parser.add_argument("--fail-rate", type=float, default=0.18, help="Fraction of lines that are failing events")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=args.lines // 2)
    rows: list[str] = []

    for i in range(args.lines):
        ts = (start + timedelta(seconds=i)).isoformat()
        module = random.choice(MODULES)
        test_name = f"{module}_test_{(i % 35) + 1:02d}"
        cycle = 1000 + i * random.randint(1, 4)
        if random.random() < args.fail_rate:
            sev = random.choice(["ERROR", "FATAL"])
            msg = random.choice(FAIL_KINDS)
            line = f"{ts} | {sev} | module={module} | test={test_name} | cycle={cycle} | {msg}"
        else:
            sev = random.choice(["INFO", "DEBUG"])
            msg = random.choice(PASS_KINDS)
            line = f"{ts} | {sev} | module={module} | test={test_name} | cycle={cycle} | {msg}"
        rows.append(line)

    out_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"Generated log file: {out_path}")
    print(f"Total lines: {args.lines}")


if __name__ == "__main__":
    main()
