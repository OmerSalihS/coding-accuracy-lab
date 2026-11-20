"""Load MTSamples notes for our chosen specialties and pick the eval subset.

- Keeps only SPECIALTIES, drops empty notes, and drops duplicate texts
  (MTSamples files some notes under more than one category).
- Marks EVAL_PER_SPECIALTY notes per specialty as in_eval_set, picked at
  random with a fixed seed so the choice is reproducible.
"""
import csv
import random
from pathlib import Path

from db import connect

ROOT = Path(__file__).resolve().parent.parent   # project root
CSV_PATH = ROOT / "data/raw/mtsamples/medical-transcriptions.csv"
SPECIALTIES = ["Orthopedic", "Cardiovascular / Pulmonary", "Gastroenterology"]
EVAL_PER_SPECIALTY = 14   # 42 candidates; you label ~25 of them
MIN_CHARS, MAX_CHARS = 800, 5000   # skip stubs and very long notes for labeling
SEED = 42


def read_notes():
    csv.field_size_limit(10**9)
    seen_texts = set()
    notes = []
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            specialty = row["medical_specialty"].strip()
            text = row["transcription"].strip()
            if specialty not in SPECIALTIES or not text or text in seen_texts:
                continue
            seen_texts.add(text)
            notes.append({
                "source_id": int(row[""]),   # the CSV's unnamed index column
                "specialty": specialty,
                "sample_name": row["sample_name"].strip(),
                "description": row["description"].strip(),
                "raw_text": text,
            })
    return notes


def pick_eval_set(notes):
    rng = random.Random(SEED)
    chosen = set()
    for specialty in SPECIALTIES:
        pool = [n["source_id"] for n in notes
                if n["specialty"] == specialty and MIN_CHARS <= len(n["raw_text"]) <= MAX_CHARS]
        chosen.update(rng.sample(pool, EVAL_PER_SPECIALTY))
    return chosen


def main():
    notes = read_notes()
    eval_ids = pick_eval_set(notes)
    with connect() as conn, conn.cursor() as cur:
        for n in notes:
            cur.execute(
                """insert into notes (source_id, specialty, sample_name, description, raw_text, in_eval_set)
                   values (%(source_id)s, %(specialty)s, %(sample_name)s, %(description)s, %(raw_text)s, %(in_eval)s)
                   on conflict (source_id) do update set in_eval_set = excluded.in_eval_set""",
                {**n, "in_eval": n["source_id"] in eval_ids},
            )
        cur.execute("select specialty, count(*), count(*) filter (where in_eval_set) "
                    "from notes group by specialty order by specialty")
        for specialty, total, in_eval in cur.fetchall():
            print(f"{specialty:30} {total:4} notes, {in_eval} in eval set")


if __name__ == "__main__":
    main()
