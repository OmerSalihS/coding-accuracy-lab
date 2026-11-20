-- Codes are stored normalized: uppercase, no dot ("M54.50" -> "M5450").
-- Display code adds the dot back after the 3rd character.

-- The clinical notes (MTSamples, public and de-identified).
create table notes (
  id           serial primary key,
  source_id    int unique not null,      -- row number in the MTSamples CSV
  specialty    text not null,
  sample_name  text,
  description  text,
  raw_text     text not null,
  in_eval_set  boolean not null default false,  -- the subset you hand-label
  source       text not null default 'mtsamples'
);

-- Official ICD-10-CM list (CDC/CMS FY2027). Used to validate codes.
create table icd10_codes (
  code         text primary key,
  is_billable  boolean not null,          -- false = category header, not billable
  short_desc   text not null,
  long_desc    text not null,
  release      text not null
);

-- Your hand-assigned answer key.
create table reference_labels (
  id          serial primary key,
  note_id     int not null references notes(id),
  code        text not null,
  code_type   text not null check (code_type in ('ICD10', 'CPT')),
  comment     text,
  labeled_by  text not null default 'omer',
  created_at  timestamp not null default now(),
  unique (note_id, code, code_type)
);

-- One row per time you run the model over the notes.
-- Lets you compare prompt v1 vs v2 vs v3.
create table runs (
  id              serial primary key,
  model_name      text not null,
  prompt_version  text not null,
  comment         text,
  created_at      timestamp not null default now()
);

-- Every code the model suggested, tagged with its run.
create table predictions (
  id                 serial primary key,
  run_id             int not null references runs(id),
  note_id            int not null references notes(id),
  code               text not null,
  code_type          text not null check (code_type in ('ICD10', 'CPT')),
  confidence         float,
  justification      text,
  validation_status  text not null default 'unchecked'
    check (validation_status in ('unchecked', 'valid_billable', 'not_billable', 'not_found')),
  created_at         timestamp not null default now()
);

-- Scores for a run, computed by the eval script.
-- specialty null = overall; match_level 'exact' or 'category' (first 3 chars).
create table eval_results (
  id                  serial primary key,
  run_id              int not null references runs(id),
  specialty           text,
  match_level         text not null check (match_level in ('exact', 'category')),
  precision           float,
  recall              float,
  f1                  float,
  hallucination_rate  float,
  notes_evaluated     int,
  created_at          timestamp not null default now()
);
