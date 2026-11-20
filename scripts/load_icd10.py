"""Load the official ICD-10-CM code list into the icd10_codes table.

The CMS "order" file is fixed-width, one code per line:
  cols 0-4   order number
  cols 6-12  code (no dot, padded to 7)
  col  14    1 = billable code, 0 = header (category, not billable)
  cols 16-75 short description
  cols 77+   long description
"""
from pathlib import Path

from db import connect

RELEASE = "FY2027"
ROOT = Path(__file__).resolve().parent.parent   # project root
ORDER_FILE = ROOT / "data/raw/icd10/extracted/icd10cm-code-descriptions-2027/icd10cm-order-2027.txt"


def parse(line: str):
    return (
        line[6:13].strip(),        # code
        line[14] == "1",           # is_billable
        line[16:76].strip(),       # short_desc
        line[77:].strip(),         # long_desc
        RELEASE,
    )


def main():
    rows = [parse(l) for l in ORDER_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    with connect() as conn, conn.cursor() as cur:
        cur.execute("delete from icd10_codes")  # safe to re-run
        with cur.copy(
            "copy icd10_codes (code, is_billable, short_desc, long_desc, release) from stdin"
        ) as copy:
            for row in rows:
                copy.write_row(row)
    billable = sum(r[1] for r in rows)
    print(f"Loaded {len(rows)} ICD-10-CM codes ({billable} billable, {len(rows) - billable} headers)")


if __name__ == "__main__":
    main()
