# Smart Fitness Session Analyzer (Assignment II)

**Student:** *Max Dyrø Finanger*
**Student number:** *409915*

---

## 1. Description

This program analyses simulated wearable fitness sessions. In Assignment I the data came from a generator and lived in Python lists and dictionaries. In Assignment II the program is a file-based application: it loads participant profiles and session measurements from CSV files, validates every row, keeps going when a row is bad, analyses each session against the participant's own baseline, and saves three output files.

For each session the program reports averages, minimums and maximums, the difference from the participant's personal reference values, whether the participant is recovering towards the end, and a classification (`resting`, `moderate_activity`, `high_activity`, `recovering`, `uncertain` or `insufficient_data`) with a written reason.

I have used Claude as a tool when developing this assignment. I used it to help me write this README and to help extend my Assignment I solution into the package structure, error handling and tests required by Assignment II.

The instructor-supplied `data_generator.py` is included unmodified. The program itself no longer uses it: it is used only by the tests, which turn its output into CSV files to check the classification rules against many seeds.

---

## 2. Installation and running

Python 3.8 or newer. Only the standard library is used (`csv`, `re`, `pathlib`, `argparse`, `statistics`, `dataclasses`, `unittest`), so there is nothing to install.

To Clone the repository:

```bash
git clone https://github.com/maxfinanger/Assignment2FitnessTracker
cd Assignment2FitnessTracker
```

From the repository root:

```bash
python3 main.py --profiles data/participants.csv \
    --sessions data/fitness_sessions.csv data/fitness_sessions_invalid.csv \
    --output output
```

All three options have defaults, so `python3 main.py` does the same thing. `--sessions` accepts one or more files. On systems where the interpreter is called `python`, use that instead of `python3`.

The program prints a completion summary (rows read, accepted and rejected per file, the classification of every session, and the files created).

**Exit codes:** `0` success; `1` finished, but at least one session file could not be read; `2` fatal (for example the profiles file is missing, or the output directory cannot be created).

To run the tests:

```bash
python3 -m unittest
```

---

## 3. Repository structure

```
main.py                       entry point (calls fitness_analyzer.cli)
fitness_analyzer/             the package
    exceptions.py             custom exception classes
    validators.py             regular-expression identifier checks
    models.py                 domain classes
    loader.py                 CSV reading, typing, row validation, grouping into sessions
    analysis.py               summaries, baseline comparison, recovery, classification
    reporting.py              turns results into text
    writers.py                writes the output files
    cli.py                    command line front end
data/                         the official CSV files (unmodified)
tests/                        unittest suite
output/                       created when the program runs
data_generator.py             instructor-supplied, unmodified (used by tests only)
```

Each module has one reason to change. A new validation rule touches `loader.py` or `models.py`; a new classification rule touches `analysis.py`; a change to the report layout touches `reporting.py`; a change to the CSV columns touches `writers.py`. `loader.py` is the only module that reads files and `writers.py` the only one that writes them, which keeps the analysis code free of file handling and easy to test.

---

## 4. Class design

| Class | Responsibility |
|---|---|
| `ParticipantProfile` | One participant's identifier, name and personal reference values. Read-only properties. |
| `RejectedRecord` | An immutable note of one refused CSV row: file, row number, every `(field, reason)` pair and the raw text. |
| `BaseObservation` | What every sensor window has: a timestamp and a signal quality, and the rule for validating them. |
| `FitnessObservation(BaseObservation)` | Adds heart rate, skin response, temperature and activity level. |
| `TrainingSession` | Owns one profile, the accepted observations and the rejected rows for one session. |
| `LoadResult` / `ProfileLoadResult` / `SessionLoadResult` | What happened to one input file: rows read, accepted, rejected, and the objects built. |

### Composition
`TrainingSession` **has** a `ParticipantProfile`, a list of `FitnessObservation` objects and a list of `RejectedRecord` objects. It is not a kind of any of them. The observations and rejections exist for that session, and the constructor refuses to build a session without a valid profile.

### Encapsulation
Baselines are stored in protected attributes behind read-only properties, because every comparison is made against them and reassigning one mid-analysis would silently invalidate a result. `ParticipantProfile` also refuses to hold an impossible baseline (for example a resting heart rate of 5). `TrainingSession` exposes its observations and rejections as **copies**; the only way in is `add_observation()` / `record_rejection()`, which check the type of what is added.

### Inheritance and overriding
`FitnessObservation` overrides `validate_fields()` and `to_dict()` and extends the parent through `super()` instead of replacing it, so the signal-quality rule is written once and cannot drift between observation types. `validate()` in the base class turns the `(field, reason)` pairs into readable strings. A test (`test_subclass_extends_rather_than_replaces_validation`) locks this in.

### Class methods and static methods
| Method | Type | Why |
|---|---|---|
| `ParticipantProfile.from_dict()` / `FitnessObservation.from_dict()` | `classmethod` | Alternative constructors from a dictionary of typed values. The dictionary key names appear in one place each. |
| `ParticipantProfile.check_baselines()` | `staticmethod` | A pure check needing no instance. The loader uses it to vet a CSV row *before* building a profile, so it can give field-level reasons. |
| `TrainingSession.summarize()` | `staticmethod` | A pure calculation over a list of numbers. |

---

## 5. Data loading

`loader.py` reads files with `with open(path, encoding="utf-8", newline="")` and `csv.reader`. `csv.reader` is used rather than `csv.DictReader` on purpose: `DictReader` silently fills missing cells with `None`, which would hide the "unexpected row length" problem the assignment asks me to detect.

* **Types.** Measurements are converted to `int` / `float`. Empty values, non-numeric text, `nan` and `inf` are all rejected.
* **Grouping.** Rows are grouped by `session_id`. Each session is linked to its `ParticipantProfile` through `participant_id`.
* **Columns are found by name**, not position, so a reordered file still loads.
* **Row numbers** are the physical line in the file (the header is line 1), which is where someone opening the file will look.
* **Blank lines** are skipped and not counted as rows.
* **Every problem in a row is collected**, not just the first, so one rejected row can list several fields.
* **Rows are attributed to a session as soon as their `session_id` is valid**, even if the rest of the row is bad. A session whose every row was rejected (`FIT-2026-102` in the official invalid file) therefore still appears in the summary as `insufficient_data` rather than disappearing.

---

## 6. Validation

### Regular expressions
Two meaningful validations, in `validators.py`:

| Value | Pattern |
|---|---|
| Participant ID | `^P\d{3}$` |
| Session ID | `^FIT-\d{4}-\d{3}$` |

Both use `re.fullmatch` **and** `re.ASCII`. `fullmatch` matters because `$` alone also matches just before a trailing newline, so `"P001\n"` would pass a `match()`. `re.ASCII` matters because `\d` otherwise accepts digits from other scripts. Both cases have tests. Regular expressions are used only for identifier *format*; numeric ranges are ordinary comparisons.

### Field rules

| Field | Rule |
|---|---|
| `participant_id` | matches its pattern **and** exists in the profiles file **and** agrees with the other rows of the same session |
| `session_id` | matches its pattern |
| `timestamp` | whole number, 0 or greater, not repeated within the session |
| `heart_rate` | number, 30–220 bpm |
| `temperature` | number, 25–42 °C |
| `activity_level` | number, 0–1 |
| `skin_response` | number, not negative |
| `signal_quality` | number, 0–1 |
| whole row | exactly as many fields as the header |
| profile baselines | same ranges as the measurements; unique participant ID; non-empty name |

Limits are inclusive. The heart rate range is slightly wider than the 35–205 "normal" range in `DATA_DESCRIPTION.md`, so genuine extremes survive while impossible values such as 265 or -15 are rejected.

### Signal quality rule
Poor signal quality is handled at three levels:

1. A value **outside 0–1** is impossible, so that **row is rejected**.
2. A value **below 0.5** is a weak but possible reading. The row is **kept and flagged** as "low confidence", because a weak signal makes a reading less trustworthy, not wrong.
3. If a session's **average** signal quality is below **0.60**, the whole session is classified `insufficient_data`. This is what happens to `FIT-2026-005`, whose values are all valid but whose signal quality is around 0.29.

---

## 7. Error handling

### Custom exceptions (all raised and all handled)

| Exception | Raised by | Handled by |
|---|---|---|
| `InvalidIdentifierError(ValueError)` | `validators.py`, when an ID fails its pattern | `loader._identifier_or_none()`, which records it as a field issue for the rejection log |
| `InvalidRecordError(ValueError)` | `loader._parse_profile_row()`, carrying every issue in a profile row | `load_profiles()`, which records a `RejectedRecord` and moves to the next row |
| `DataFileError(Exception)` | `read_csv_table()` and the writers, wrapping low-level failures with the path and a plain reason | `cli.run()` for session files (skip that file, continue); `cli.main()` for fatal cases |
| `ValidationError(ValueError)` | model constructors, when an object cannot be built | the loader, as a defensive fallback that becomes a rejection |

### Targeted `try` / `except`
Errors are caught only where the program can add context or recover:

* `FileNotFoundError`, `PermissionError`, `IsADirectoryError`, `UnicodeDecodeError`, `csv.Error` in `read_csv_table()` become one `DataFileError` naming the file.
* `ValueError` from `int()` / `float()` in `_convert_fields()` becomes a field issue such as `'fast' cannot be converted to a number`.
* `KeyError` on the profile lookup becomes `unknown participant 'P999'`; `KeyError` in `from_dict()` becomes a `ValidationError` naming the missing field.
* `FileExistsError`, `PermissionError` and `OSError` in the writers become `DataFileError`.
* `StopIteration` on an empty file becomes "file is empty (no header row)".

There is no `except Exception`, no bare `except`, and no empty `except` block. A genuine programming error (a `TypeError` or `AttributeError`, say) is not caught and will surface with a traceback rather than being hidden.

### What happens when things go wrong

| Situation | Behaviour |
|---|---|
| A row is invalid | recorded in `rejected_records.txt`, skipped, run continues |
| A session file is missing or unreadable | listed under "files that could not be read", other files still processed, exit code 1 |
| The profiles file is missing or unreadable | error on stderr, exit code 2 (nothing can be analysed without baselines) |
| The output directory does not exist | created, including parents |
| The output path is a file, or is not writable | clear error, exit code 2 |

---

## 8. Classification rules

Unchanged from Assignment I, and applied in this order:

1. **`insufficient_data`** if fewer than 3 rows are usable, or more than 30% of rows were rejected, or average signal quality is below 0.60. The gate runs first so an unusable session is never given an authoritative-looking label.
2. **`recovering`** if heart rate fell at least 12 bpm and activity at least 0.10 from the session's peak block to its closing block, and closing activity is below 0.65.
3. **`resting`** if heart rate is at most 10 bpm above baseline and activity at most 0.25.
4. **`high_activity`** if heart rate is at least 45 bpm above baseline and activity at least 0.65.
5. **`moderate_activity`** if heart rate is at least 15 bpm above baseline and activity at least 0.30.
6. **`uncertain`** otherwise.

Rows rejected during loading count towards the 30% limit, so the program distinguishes insufficient usable data from a meaningful result. The recovery check compares the closing third of the session with the highest sustained earlier block rather than with the opening third, because a session that starts calm, peaks and then falls away would otherwise look flat. The reasoning is in the docstring of `detect_recovery()`.

The result of `analyze_session()` is one nested dictionary with the keys `session_id`, `source_file`, `participant`, `data_quality`, `summaries`, `baseline_comparison`, `recovery` and `classification`.

Results for the official files:

| Session | Participant | Usable rows | Classification |
|---|---|---|---|
| FIT-2026-001 | P001 | 6/6 | resting |
| FIT-2026-002 | P002 | 6/6 | moderate_activity |
| FIT-2026-003 | P003 | 6/6 | high_activity |
| FIT-2026-004 | P001 | 6/6 | recovering |
| FIT-2026-005 | P002 | 5/5 | insufficient_data (average signal quality 0.29) |
| FIT-2026-101 | P001 | 1/5 | insufficient_data |
| FIT-2026-102 | P002 | 0/4 | insufficient_data |
| FIT-2026-103 | P003 | 0/1 | insufficient_data |

---

## 9. Output files

The `output/` directory is created if it does not exist. Files are overwritten on every run, contain no timestamps and list sessions in input order, so running the program twice gives byte-identical files with no manual cleanup.

* **`analysis_summary.csv`** has one row per session: identifiers, row counts, invalid fraction, signal quality, averages, deltas from baseline, whether recovery was detected, the classification and its explanation.
* **`analysis_report.txt`** starts with an overview table, then a full readable report for each session (reference values, data quality, rejected rows, summaries, baseline comparison, recovery check, classification and reason).
* **`rejected_records.txt`** lists, for every rejected row, the source file, row number, each failing field with the reason, and the raw row. It also lists any files that could not be read and any sessions that could not be linked to a participant.

---

## 10. Testing

`python3 -m unittest` runs 104 tests with no third-party runner:

* `test_validators.py`: valid and invalid IDs, trailing newline, non-ASCII digits, non-strings.
* `test_models.py`: encapsulation, composition, inheritance and overriding, and validation **boundaries** (values exactly on and just beyond every limit).
* `test_loader.py`: the official files (row-by-row expectations for the invalid file), wrong row lengths, unconvertible and missing values, duplicate timestamps, participant conflicts, profile validation, boundary values, and **file errors** (missing file, permission denied, directory, empty file, missing column, bad encoding, malformed CSV, BOM, reordered columns).
* `test_analysis.py`: calculations, recovery cases, data-quality gate boundaries (exactly 3 usable rows, exactly 30% rejected, signal quality 0.60 vs 0.59), threshold edges, and the generator's five scenarios run through real CSV files across several seeds.
* `test_cli.py`: output directory creation, the three files' contents, identical output across two runs, stale files overwritten, missing profiles file (fatal), missing session file (skipped), unwritable output.

The scenario tests write the generator's output to temporary CSV files and load it through the real loader, so they exercise the full pipeline including rejection of the poor-quality scenario's deliberately broken rows. Running 300 seeds of each of the five scenarios classified all 1,500 sessions correctly.

---

## 11. Known limitations

* **Thresholds are hand set, not learned.** They are tuned against one simulated data source and would need re-examining on real labelled data.
* **A row is rejected if any one field is bad**, even when the others are fine. Salvaging partial rows would recover more data at the cost of more complicated bookkeeping.
* **A session's participant is decided by its first row that names a valid, known participant.** Later rows that disagree are rejected rather than the first row being second-guessed.
* **Session IDs are grouped per file.** If the same ID appeared in two files it would be analyzed as two separate sessions.
* **Skin response and temperature are reported but do not affect classification**; the rules use heart rate and activity level only.
* **Recovery detection needs at least three usable rows** and compares thirds of the session, so it is noisy on very short sessions.
* **Heart rates print as `68.0`** rather than `68`, because all measurements are converted to `float`.
