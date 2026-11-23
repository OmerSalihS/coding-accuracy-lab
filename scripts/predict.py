"""Ask Gemini to suggest codes for each note, validate them, and save them.

Usage:
  python scripts/predict.py --list-models          # which models your key can use
  python scripts/predict.py --note 5               # try one note, print the result, save nothing
  python scripts/predict.py --prompt v1            # run the whole eval set and save a run

Every full run creates one row in `runs` (model + prompt version) and one row
in `predictions` per suggested code. Each code is checked against the official
ICD-10-CM list as it is saved, so made-up codes are caught immediately.
"""
import argparse
import os
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from db import connect, display_code, normalize_code

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
load_dotenv(ROOT / ".env")   # reads GEMINI_API_KEY (and GEMINI_MODEL) from .env

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
MAX_ATTEMPTS = 3   # retry a failed API call this many times


# The exact shape we want back from Gemini. The SDK turns these classes into a
# JSON schema, Gemini is forced to follow it, and we get Python objects back.
class SuggestedCode(BaseModel):
    code: str
    code_type: Literal["ICD10", "CPT"]
    confidence: float
    justification: str


class CodingResult(BaseModel):
    suggested_codes: list[SuggestedCode]
    missing_documentation_flags: list[str]


def load_prompt(version):
    return (PROMPTS_DIR / f"{version}.txt").read_text(encoding="utf-8")


def ask_gemini(client, model, prompt, note_text):
    """Send one note to Gemini and return a CodingResult. Retries on errors."""
    config = types.GenerateContentConfig(
        system_instruction=prompt,
        response_mime_type="application/json",
        response_schema=CodingResult,
        temperature=0,   # least random, so reruns give (nearly) the same answer
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.models.generate_content(
                model=model, contents=f"Clinical note:\n\n{note_text}", config=config
            )
            if response.parsed is None:
                raise ValueError(f"could not parse response: {response.text[:200]}")
            return response.parsed
        except Exception as e:
            # A 4xx error (bad model name, bad key, bad request) won't fix itself,
            # so stop right away. 429 means "too many requests": waiting helps.
            is_client_error = isinstance(e, errors.ClientError) and e.code != 429
            if attempt == MAX_ATTEMPTS or is_client_error:
                raise
            wait = 5 * attempt
            print(f"    attempt {attempt} failed ({e}); retrying in {wait}s")
            time.sleep(wait)


def validate(cur, code, code_type):
    """Look a code up in the official list. Returns a validation_status value."""
    if code_type != "ICD10":
        return "unchecked"   # no free CPT list to check against
    cur.execute("select is_billable from icd10_codes where code = %s", (code,))
    row = cur.fetchone()
    if row is None:
        return "not_found"   # the code does not exist: a hallucination
    return "valid_billable" if row[0] else "not_billable"


def clean_codes(result):
    """Normalize codes and drop duplicates the model repeated within one note."""
    seen = set()
    for s in result.suggested_codes:
        code = normalize_code(s.code) if s.code_type == "ICD10" else s.code.strip()
        if not code or (code, s.code_type) in seen:
            continue
        seen.add((code, s.code_type))
        yield code, s


def try_one_note(client, model, prompt, note_id):
    """Print Gemini's answer for one note without saving anything."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select raw_text, in_eval_set from notes where id = %s", (note_id,))
        text, in_eval = cur.fetchone()
        if in_eval:
            print("Warning: this note is in the eval set. Seeing the model's codes "
                  "before you label it can bias your labels.\n")
        result = ask_gemini(client, model, prompt, text)
        for code, s in clean_codes(result):
            status = validate(cur, code, s.code_type)
            shown = display_code(code) if s.code_type == "ICD10" else code
            print(f"  {s.code_type:5} {shown:9} {s.confidence:.2f}  {status:15} {s.justification}")
        for flag in result.missing_documentation_flags:
            print(f"  missing doc: {flag}")


def run_eval_set(client, model, prompt_version, comment):
    prompt = load_prompt(prompt_version)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("insert into runs (model_name, prompt_version, comment) values (%s, %s, %s) "
                    "returning id", (model, prompt_version, comment))
        run_id = cur.fetchone()[0]
        conn.commit()

        cur.execute("select id from notes where in_eval_set order by id")
        note_ids = [r[0] for r in cur.fetchall()]
        print(f"Run {run_id}: {model}, prompt {prompt_version}, {len(note_ids)} notes")

        failed = []
        for i, note_id in enumerate(note_ids, start=1):
            cur.execute("select raw_text from notes where id = %s", (note_id,))
            text = cur.fetchone()[0]
            try:
                result = ask_gemini(client, model, prompt, text)
            except Exception as e:
                print(f"  [{i}/{len(note_ids)}] note {note_id}: FAILED ({e})")
                failed.append(note_id)
                continue
            count = 0
            for code, s in clean_codes(result):
                cur.execute(
                    """insert into predictions
                       (run_id, note_id, code, code_type, confidence, justification, validation_status)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    (run_id, note_id, code, s.code_type, s.confidence, s.justification,
                     validate(cur, code, s.code_type)),
                )
                count += 1
            conn.commit()   # save after every note, so a crash keeps finished work
            # Only counts are printed, so you don't see codes for notes you haven't labeled.
            print(f"  [{i}/{len(note_ids)}] note {note_id}: {count} codes")

        cur.execute("""select validation_status, count(*) from predictions
                       where run_id = %s group by validation_status order by 1""", (run_id,))
        print(f"\nRun {run_id} done. Validation:")
        for status, n in cur.fetchall():
            print(f"  {status:15} {n}")
        if failed:
            print(f"Failed notes (no predictions saved): {failed}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="v1", help="prompt version, a file in prompts/")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--note", type=int, help="try a single note and print the result")
    parser.add_argument("--comment", help="a note to yourself stored with the run")
    parser.add_argument("--list-models", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    if args.list_models:
        for m in client.models.list():
            if "generateContent" in (m.supported_actions or []):
                print(m.name.removeprefix("models/"))
    elif args.note:
        try_one_note(client, args.model, load_prompt(args.prompt), args.note)
    else:
        run_eval_set(client, args.model, args.prompt, args.comment)


if __name__ == "__main__":
    main()
