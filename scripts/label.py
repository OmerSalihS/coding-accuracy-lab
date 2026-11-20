"""Interactive tool for hand-labeling eval notes with reference codes.

Usage:
  python scripts/label.py              # open the next unlabeled eval note
  python scripts/label.py --note 12    # open a specific note by id
  python scripts/label.py --status     # show labeling progress

Commands inside a note:
  M54.50          add an ICD-10-CM code (checked against the official list)
  cpt 27447       add a CPT code (no official list to check against)
  ? knee pain     search ICD-10-CM descriptions
  rm M54.50       remove a label
  show            print the note again
  next            save and go to the next unlabeled note
  quit            exit
"""
import argparse
import textwrap

from db import connect, display_code, normalize_code


def print_note(cur, note_id):
    cur.execute("select specialty, sample_name, description, raw_text from notes where id = %s",
                (note_id,))
    specialty, name, desc, text = cur.fetchone()
    print("\n" + "=" * 80)
    print(f"NOTE {note_id} | {specialty} | {name}")
    print(f"Summary: {desc}")
    print("=" * 80)
    # MTSamples uses ",  " where line breaks used to be.
    for para in text.replace(",  ", "\n").split("\n"):
        print(textwrap.fill(para.strip(), 80))
    print("=" * 80)
    print_labels(cur, note_id)


def print_labels(cur, note_id):
    cur.execute("""select r.code, r.code_type, coalesce(i.short_desc, '')
                   from reference_labels r left join icd10_codes i on i.code = r.code
                   where r.note_id = %s order by r.code_type desc, r.id""", (note_id,))
    rows = cur.fetchall()
    print("Current labels:", "none" if not rows else "")
    for code, code_type, desc in rows:
        print(f"  {code_type:5} {display_code(code) if code_type == 'ICD10' else code:9} {desc}")


def search(cur, query):
    words = query.split()
    if not words:
        return
    where = " and ".join(["long_desc ilike %s"] * len(words))
    cur.execute(f"""select code, is_billable, long_desc from icd10_codes
                    where {where} order by is_billable desc, code limit 20""",
                [f"%{w}%" for w in words])
    for code, billable, desc in cur.fetchall():
        print(f"  {display_code(code):9} {'   ' if billable else '(H)'} {desc}")
    print("  (H) = header, not billable")


def add_icd10(cur, note_id, raw):
    code = normalize_code(raw)
    cur.execute("select is_billable, long_desc from icd10_codes where code = %s", (code,))
    row = cur.fetchone()
    if row is None:
        print(f"  {raw} is not in the FY2027 ICD-10-CM list. Not added.")
        return
    billable, desc = row
    if not billable:
        print(f"  {display_code(code)} is a header ({desc}), not billable. Pick a more specific code:")
        cur.execute("""select code, long_desc from icd10_codes
                       where code like %s and is_billable order by code limit 15""", (code + "%",))
        for child, child_desc in cur.fetchall():
            print(f"    {display_code(child):9} {child_desc}")
        return
    insert_label(cur, note_id, code, "ICD10")
    print(f"  + ICD10 {display_code(code)}  {desc}")


def insert_label(cur, note_id, code, code_type):
    cur.execute("""insert into reference_labels (note_id, code, code_type) values (%s, %s, %s)
                   on conflict do nothing""", (note_id, code, code_type))


def label_note(conn, note_id):
    """Returns False if the user wants to quit."""
    with conn.cursor() as cur:
        print_note(cur, note_id)
        while True:
            cmd = input(f"[note {note_id}] > ").strip()
            low = cmd.lower()
            if not cmd:
                continue
            if low == "quit":
                return False
            if low == "next":
                return True
            if low == "show":
                print_note(cur, note_id)
            elif cmd.startswith("?"):
                search(cur, cmd[1:])
            elif low.startswith("cpt "):
                code = cmd[4:].strip()
                insert_label(cur, note_id, code, "CPT")
                print(f"  + CPT {code}  (not validated, no free CPT list)")
            elif low.startswith("rm "):
                cur.execute("delete from reference_labels where note_id = %s and code in (%s, %s)",
                            (note_id, normalize_code(cmd[3:]), cmd[3:].strip()))
                print(f"  removed {cur.rowcount} label(s)")
            else:
                add_icd10(cur, note_id, cmd)
            conn.commit()  # save after every command


def next_unlabeled(cur, skip):
    cur.execute("""select n.id from notes n
                   where n.in_eval_set
                     and not exists (select 1 from reference_labels r where r.note_id = n.id)
                     and not (n.id = any(%s))
                   order by n.id limit 1""", (list(skip),))
    row = cur.fetchone()
    return row[0] if row else None


def status(cur):
    cur.execute("""select n.specialty,
                          count(distinct n.id),
                          count(distinct r.note_id)
                   from notes n left join reference_labels r on r.note_id = n.id
                   where n.in_eval_set group by n.specialty order by n.specialty""")
    for specialty, total, labeled in cur.fetchall():
        print(f"{specialty:30} {labeled}/{total} labeled")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--note", type=int)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    with connect() as conn:
        if args.status:
            with conn.cursor() as cur:
                status(cur)
            return
        if args.note:
            label_note(conn, args.note)
            return
        seen = set()
        while True:
            with conn.cursor() as cur:
                note_id = next_unlabeled(cur, seen)
            if note_id is None:
                print("No unlabeled eval notes left.")
                return
            seen.add(note_id)
            if not label_note(conn, note_id):
                return


if __name__ == "__main__":
    main()
