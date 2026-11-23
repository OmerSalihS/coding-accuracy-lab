"""Score a run's predictions against your reference labels.

Usage:
  python scripts/evaluate.py              # score the latest run
  python scripts/evaluate.py --run 3      # score a specific run
  python scripts/evaluate.py --details    # also list every hit, miss and wrong code per note
  python scripts/evaluate.py --compare    # one line per run scored so far (v1 vs v2 vs v3)

Only notes you have labeled are scored. A note counts as labeled once it has at
least one label of any type, so a note with only a CPT code (no documented
diagnosis) is still scored: any diagnosis the model suggests for it is wrong.

ICD-10 is the main track and is saved to eval_results. CPT is printed only.
"""
import argparse
from collections import defaultdict

from db import connect, display_code


# ---------- the math (pure functions, no database) ----------

def to_level(codes, level):
    """'exact' keeps codes as they are; 'category' keeps only the first 3 characters."""
    return set(codes) if level == "exact" else {c[:3] for c in codes}


def count_matches(gold_by_note, guess_by_note, note_ids, level):
    """Add up hits (tp), wrong guesses (fp) and misses (fn) across notes."""
    tp = fp = fn = 0
    for n in note_ids:
        gold = to_level(gold_by_note.get(n, set()), level)
        guess = to_level(guess_by_note.get(n, set()), level)
        tp += len(gold & guess)    # suggested and correct
        fp += len(guess - gold)    # suggested but not in your labels
        fn += len(gold - guess)    # in your labels but not suggested
    return tp, fp, fn


def metrics(tp, fp, fn):
    """Precision, recall, F1. None when there is nothing to divide by."""
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision and recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0 if precision is not None and recall is not None else None
    return precision, recall, f1


def pct(x):
    return "   —  " if x is None else f"{x:6.1%}"


# ---------- reading from the database ----------

def load_labels(cur):
    """Returns ({note_id: specialty}, {code_type: {note_id: set of codes}}) for labeled eval notes."""
    cur.execute("""select n.id, n.specialty, r.code, r.code_type
                   from notes n join reference_labels r on r.note_id = n.id
                   where n.in_eval_set""")
    specialty = {}
    labels = {"ICD10": defaultdict(set), "CPT": defaultdict(set)}
    for note_id, spec, code, code_type in cur.fetchall():
        specialty[note_id] = spec
        labels[code_type][note_id].add(code)
    return specialty, labels


def load_predictions(cur, run_id):
    """Returns {code_type: {note_id: set of codes}} and the ICD validation statuses per note."""
    cur.execute("select note_id, code, code_type, validation_status from predictions where run_id = %s",
                (run_id,))
    preds = {"ICD10": defaultdict(set), "CPT": defaultdict(set)}
    statuses = defaultdict(dict)   # note_id -> {code: status}, ICD only
    for note_id, code, code_type, status in cur.fetchall():
        preds[code_type][note_id].add(code)
        if code_type == "ICD10":
            statuses[note_id][code] = status
    return preds, statuses


def latest_run(cur):
    cur.execute("select max(id) from runs")
    return cur.fetchone()[0]


# ---------- the report ----------

def evaluate(cur, run_id, show_details):
    cur.execute("select model_name, prompt_version, created_at from runs where id = %s", (run_id,))
    row = cur.fetchone()
    if row is None:
        raise SystemExit(f"There is no run {run_id}.")
    model, prompt_version, created = row

    specialty, labels = load_labels(cur)
    preds, statuses = load_predictions(cur, run_id)
    note_ids = sorted(specialty)
    if not note_ids:
        raise SystemExit("No labeled notes yet. Label some with scripts/label.py first.")

    # Groups to score: everything together (specialty None), then each specialty.
    groups = [(None, note_ids)]
    for spec in sorted(set(specialty.values())):
        groups.append((spec, [n for n in note_ids if specialty[n] == spec]))

    print(f"Run {run_id}: {model}, prompt {prompt_version}, {created:%Y-%m-%d %H:%M}")
    print(f"Scoring {len(note_ids)} labeled notes. ICD-10-CM:\n")
    print(f"  {'group':28} {'match':9} {'notes':>5} {'precision':>9} {'recall':>7} {'F1':>7} {'made-up':>8}")

    cur.execute("delete from eval_results where run_id = %s", (run_id,))   # safe to re-run
    for spec, ids in groups:
        # Hallucination rate: share of suggested ICD codes that don't exist at all.
        all_statuses = [s for n in ids for s in statuses.get(n, {}).values()]
        made_up = all_statuses.count("not_found") / len(all_statuses) if all_statuses else None
        for level in ("exact", "category"):
            p, r, f1 = metrics(*count_matches(labels["ICD10"], preds["ICD10"], ids, level))
            cur.execute("""insert into eval_results (run_id, specialty, match_level, precision, recall,
                                                     f1, hallucination_rate, notes_evaluated)
                           values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (run_id, spec, level, p, r, f1, made_up, len(ids)))
            name = spec or "ALL"
            print(f"  {name:28} {level:9} {len(ids):5} {pct(p):>9} {pct(r):>7} {pct(f1):>7} {pct(made_up):>8}")

    # Extra lines that explain the headline numbers.
    all_statuses = [s for n in note_ids for s in statuses.get(n, {}).values()]
    headers = all_statuses.count("not_billable")
    print(f"\n  Suggested ICD codes: {len(all_statuses)} "
          f"({all_statuses.count('not_found')} made up, {headers} real but only a category header)")
    no_dx = [n for n in note_ids if not labels["ICD10"].get(n)]
    if no_dx:
        guessed = sum(len(preds["ICD10"].get(n, ())) for n in no_dx)
        print(f"  Notes with no documented diagnosis: {len(no_dx)}; "
              f"the model still suggested {guessed} diagnosis codes for them")
    empty = [n for n in note_ids if not preds["ICD10"].get(n) and not preds["CPT"].get(n)]
    if empty:
        print(f"  Notes with no predictions at all (failed in predict.py?): {empty}")

    cpt_ids = [n for n in note_ids if labels["CPT"].get(n)]
    if cpt_ids:
        p, r, f1 = metrics(*count_matches(labels["CPT"], preds["CPT"], cpt_ids, "exact"))
        print(f"\nCPT (secondary, {len(cpt_ids)} notes with a CPT label, not saved): "
              f"precision {pct(p).strip()}, recall {pct(r).strip()}, F1 {pct(f1).strip()}")

    if show_details:
        print_details(cur, note_ids, specialty, labels, preds, statuses)


def print_details(cur, note_ids, specialty, labels, preds, statuses):
    """Per note: hits, wrong guesses and misses. This is where prompt ideas come from."""
    print("\nPer note (+ hit, - wrong guess, ? missed):")
    for n in note_ids:
        gold, guess = labels["ICD10"].get(n, set()), preds["ICD10"].get(n, set())
        print(f"\n  note {n} ({specialty[n]})")
        for code in sorted(gold & guess):
            print(f"    + {display_code(code)}")
        for code in sorted(guess - gold):
            print(f"    - {display_code(code):9} ({statuses[n][code]})")
        for code in sorted(gold - guess):
            print(f"    ? {display_code(code)}")


def compare(cur):
    """One line per scored run: the overall exact-match numbers."""
    cur.execute("""select r.id, r.model_name, r.prompt_version, e.notes_evaluated,
                          e.precision, e.recall, e.f1, e.hallucination_rate
                   from eval_results e join runs r on r.id = e.run_id
                   where e.specialty is null and e.match_level = 'exact'
                   order by r.id""")
    rows = cur.fetchall()
    if not rows:
        print("No runs scored yet.")
        return
    print(f"  {'run':>3} {'model':22} {'prompt':6} {'notes':>5} {'precision':>9} {'recall':>7} {'F1':>7} {'made-up':>8}")
    for run_id, model, prompt, n, p, r, f1, h in rows:
        print(f"  {run_id:3} {model:22} {prompt:6} {n:5} {pct(p):>9} {pct(r):>7} {pct(f1):>7} {pct(h):>8}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=int, help="run id (default: the latest run)")
    parser.add_argument("--details", action="store_true", help="list hits, misses and wrong codes per note")
    parser.add_argument("--compare", action="store_true", help="compare all scored runs")
    args = parser.parse_args()

    with connect() as conn, conn.cursor() as cur:
        if args.compare:
            compare(cur)
            return
        run_id = args.run or latest_run(cur)
        if run_id is None:
            raise SystemExit("No runs yet. Run scripts/predict.py first.")
        evaluate(cur, run_id, args.details)


if __name__ == "__main__":
    main()
