# MVP Plan: AI Chart Coding Assistant with Measured Accuracy

**Goal:** build a small, honest demo of an LLM reading clinical notes and suggesting CPT/ICD-10 codes, with a real, hand-labeled accuracy score attached to it. The accuracy-measurement piece is the actual point — it's a direct echo of Charta's core product problem (AI suggesting codes is only useful if someone can say how often it's right), and it's the same thing you already did for your capstone forecasting model.

Target build time: 7-10 days, a couple hours a day. Keep scope tight — a small, honestly-measured thing beats a bigger, hand-wavy one.

---

## 1. Tech stack

Chosen to overlap with what Charta's engineers actually use, and what you already know:

- **Backend:** Python + FastAPI (you already know this from your capstone)
- **LLM:** Google Gemini API (you already have working experience with this) — or swap in Claude/OpenAI if you'd rather show range; the pipeline design doesn't change
- **Database:** PostgreSQL (matches their stack; stores notes, predictions, your reference labels, and results)
- **Frontend:** Next.js + TypeScript + React (matches their preferred stack; you already used Next.js on your AI Hardware Reliability Platform project)
- **Containerization:** Docker, with a `docker-compose.yml` tying backend + Postgres + frontend together
- **Optional deploy:** Railway or Render (free tier) for the backend/DB, Vercel for the Next.js frontend — gives you a live link instead of only a local demo

---

## 2. Data — and the compliance note that matters most

Use the **MTSamples dataset** (Kaggle: "Medical Transcriptions" by tboyle10) — around 5,000 de-identified, publicly shared medical transcription samples across specialties. This is the standard public dataset for exactly this kind of project and contains **no real patient data**.

- Pick 2-3 specialties to keep scope sane (e.g., Orthopedic, Cardiovascular/Pulmonary, Surgery)
- Pull ~40-50 notes total from those categories

**Say this explicitly when you present it:** you used a public, de-identified dataset and never touched real PHI. At a healthcare compliance company, that judgment call is itself a signal worth making visible, not just a footnote.

---

## 3. Ground truth — be honest about what this is

MTSamples notes don't come with official billing codes attached, so you'll need to assign reference codes yourself to a subset (20-30 notes is enough) using public lookup tools:

- [icd10data.com](https://www.icd10data.com) for ICD-10-CM codes
- AAPC's free code lookup for CPT codes

**Important framing for the meeting:** you are not a certified coder, so call these "reference labels I assigned using public coding guidelines," not professional-grade ground truth. Being upfront about that limitation will land far better with a head engineer at a coding-accuracy company than quietly implying more authority than you have. You can optionally use an LLM as a second opinion while labeling, but the final call should be yours — the eval is meaningless if the labels come from the same kind of system you're evaluating.

---

## 4. Architecture

```
MTSamples CSV
      |
      v
[ingest script] --> Postgres: notes table
      |
      v
[FastAPI service] --(prompt)--> [Gemini API]
      |                              |
      |<---- structured JSON --------|
      v
Postgres: predictions table
      |
      v
[validation step] -- checks every suggested code against
      |              a real CPT/ICD-10 code list (catches
      |              hallucinated codes before they count)
      v
Postgres: validated predictions
      |
      v
[eval script] -- compares predictions vs your reference labels
      |            --> precision / recall / F1, per-code and overall
      v
Next.js dashboard: note viewer + suggested codes + confidence
                    + your reference labels + accuracy metrics
```

---

## 5. Database schema (Postgres)

```sql
create table notes (
  id serial primary key,
  specialty text,
  raw_text text,
  source text default 'mtsamples'
);

create table reference_labels (
  id serial primary key,
  note_id int references notes(id),
  code text,          -- e.g. "99213" or "M54.5"
  code_type text,     -- 'CPT' or 'ICD10'
  labeled_by text default 'omer'
);

create table predictions (
  id serial primary key,
  note_id int references notes(id),
  code text,
  code_type text,
  confidence float,
  model_name text,
  is_valid_code boolean,   -- set by the validation step
  created_at timestamp default now()
);

create table eval_runs (
  id serial primary key,
  run_label text,
  precision float,
  recall float,
  f1 float,
  notes_evaluated int,
  created_at timestamp default now()
);
```

---

## 6. LLM prompt design — and guarding against hallucinated codes

Use structured/JSON output mode (Gemini supports this) rather than free text, so you get reliably parseable results:

```
System: You are reviewing a clinical note to suggest relevant CPT and ICD-10-CM
billing codes. Only suggest codes you are reasonably confident are supported
by the documentation. For each code, include a confidence score (0-1) and a
one-sentence justification quoting the relevant part of the note.

Output strict JSON:
{
  "suggested_codes": [
    {"code": "...", "code_type": "CPT"|"ICD10", "confidence": 0.0-1.0, "justification": "..."}
  ],
  "missing_documentation_flags": ["..."]
}

Note:
<<note text here>>
```

**The hallucination guard (this is the detail that will actually impress a technical interviewer):** LLMs will sometimes invent codes that don't exist. Download the real CPT and ICD-10-CM code lists (CMS publishes ICD-10-CM free; CPT codes are AMA-licensed so for a demo you can use a smaller public reference list or a Python package like `icd10-cm`) and validate every single code the model returns against that list before it's allowed into your `predictions` table as `is_valid_code = true`. Report the hallucination rate as its own metric — "X% of suggested codes weren't real codes at all" is a genuinely sharp thing to be able to say out loud in that meeting, because it shows you understand LLM reliability failure modes in a regulated domain, not just how to call an API.

---

## 7. Evaluation methodology

For your labeled subset, compute:

- **Precision:** of codes the model suggested, what fraction were correct (matched your reference labels)?
- **Recall:** of codes that should have been there (per your reference labels), what fraction did the model actually catch?
- **F1:** harmonic mean of the two
- **Hallucination rate:** % of suggested codes that failed the real-code-list validation
- Break it down **per specialty** if you have enough labeled notes — interesting if accuracy differs between, say, orthopedic and cardiovascular notes

Be ready to say plainly where it's weak. A confident, specific "it's at 71% precision and does worse on notes with multiple comorbidities, here's why I think that is" beats an inflated, vague "it works great."

---

## 8. Build order (7-10 days, a couple hours/day)

1. **Day 1-2:** Set up repo, Docker Compose skeleton (Postgres + FastAPI), download and explore MTSamples, pick your subset of notes, load them into `notes` table.
2. **Day 3:** Hand-label reference codes for your eval subset (20-30 notes). This takes longer than it sounds — budget real time for it.
3. **Day 4-5:** Build the FastAPI endpoint that calls Gemini with the structured prompt, parses the JSON, stores predictions.
4. **Day 6:** Build the code-list validation step and hallucination-rate tracking.
5. **Day 7:** Write the eval script — compute precision/recall/F1/hallucination rate against your reference labels.
6. **Day 8-9:** Build the Next.js dashboard — note list, note detail view with suggested vs. reference codes side by side, and an aggregate metrics panel.
7. **Day 10:** Polish, write a short README explaining the data source and labeling caveat, optionally deploy, record a 2-minute demo video as a backup in case live demo has issues.

---

## 9. How to present it in the meeting

- Lead with the problem framing, not the tech: "An AI suggesting billing codes is only useful if someone can say how often it's actually right — so I built the measurement alongside the model, not after it."
- State the data provenance up front (public, de-identified, no PHI) before anyone has to ask.
- Show the real numbers, including the weak spots and the hallucination rate.
- Have the repo/demo ready to share, but don't over-polish the pitch — a specific, honestly-measured small project reads as more credible than a big claim with no numbers behind it.
