#!/usr/bin/env python3
"""Generate a large synthetic CSV for the scale / large-file demo.

Streams rows to disk (constant memory) so you can create multi-million-row
files. Every row has an ``Email`` and ``Phone`` column with realistic-looking
values to exercise the regex replacement.

Examples
--------
    python scripts/generate_dataset.py --rows 1000000 --out data/uploads/big.csv
    python scripts/generate_dataset.py --rows 5000000 --out data/uploads/huge.csv
"""
from __future__ import annotations

import argparse
import csv
import random

FIRST = ["John", "Jane", "Alice", "Bob", "Carol", "David", "Eve", "Frank",
         "Grace", "Heidi", "Ivan", "Judy", "Mallory", "Niaj", "Olivia", "Peggy"]
LAST = ["Doe", "Smith", "Brown", "Jones", "Garcia", "Miller", "Davis", "Lopez",
        "Wilson", "Martin", "Lee", "Walker", "Hall", "Allen", "Young", "King"]
DOMAINS = ["example.com", "domain.com", "website.org", "mail.net", "corp.io"]
NOTES = ["follow up next week", "VIP customer", "needs callback",
         "renewed plan", "trial user", "no notes"]


def make_row(i: int) -> list:
    first = random.choice(FIRST)
    last = random.choice(LAST)
    sep = random.choice([".", "_", ""])
    email = f"{first.lower()}{sep}{last.lower()}@{random.choice(DOMAINS)}"
    phone = f"({random.randint(200, 999)}) {random.randint(200, 999)}-{random.randint(1000, 9999)}"
    return [i, f"{first} {last}", email, phone, random.choice(NOTES)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--out", default="data/uploads/big.csv")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ID", "Name", "Email", "Phone", "Notes"])
        for i in range(1, args.rows + 1):
            writer.writerow(make_row(i))
            if i % 500_000 == 0:
                print(f"  ...{i:,} rows")

    print(f"Wrote {args.rows:,} rows to {args.out}")


if __name__ == "__main__":
    main()
