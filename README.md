# Local Lens

Local single-user data analyst with a LangGraph SQL agent and an evaluation harness. Inference uses downloaded Ollama models. The application binds to 127.0.0.1.

## Setup and run (Windows)

Requirements: Python 3.11+, Node.js 20+ for frontend builds, Ollama. MySQL is optional.

From the project root:

```powershell
ollama pull qwen2.5-coder:1.5b
ollama pull qwen2.5-coder:3b
ollama pull nomic-embed-text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\scripts\setup.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\scripts\start.ps1
```

Open http://127.0.0.1:8000 and use the displayed workspace key. Keep the server terminal open. Subsequent runs need only start.ps1. Ollama usually runs in the background; run `ollama serve` only if it is stopped. Configuration is backend/.env; setup copies backend/.env.example if absent.

To stop only this installation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\scripts\stop.ps1
```

Do not stop or restart during an evaluation: unfinished runs become interrupted. No scheduled overnight job or follow-up is configured.

## Directory

```text
backend/
  app/                    API, agent, validation, execution, feedback, harness
  evals/                  curated cases, review record, scoring and staged runner
  scripts/                setup.ps1, start.ps1, stop.ps1
  requirements.lock.txt   pinned runtime dependencies
  .env.example            configuration template
  LICENSE
  .env                    private configuration (generated)
  .venv/                  runtime environment (generated)
  storage/                private data, keys, history, audits, reports (generated)
frontend/
  src/                    React source
  dist/                   built frontend required by the backend
  package.json
  package-lock.json
  index.html
  vite.config.js
README.md
.gitignore
```

Frontend node_modules is needed only to rebuild; setup recreates it with npm ci. It is not needed to run the built application. Tests and redundant development files were archived outside this installation. Runtime data is preserved, not considered disposable clutter.

## Datasets and agent

Add CSV, a supported SQL file, local MySQL or an existing SQLite file. CSV becomes a separate data table; SQL uploads become isolated SQLite snapshots. SQL upload accepts CREATE TABLE and literal INSERT VALUES, not arbitrary dumps, views, routines, DROP or executable expressions. Indexes, defaults, checks and MySQL engine semantics are not reproduced.

MySQL connections require localhost and a dedicated SELECT-only account; root/write/admin accounts are rejected. SQLite connections require an absolute path within LOCAL_FILE_ROOTS. Connected sources read current data; uploads remain snapshots. Select one dataset per question; cross-dataset joins are unsupported.

Schema access controls approved columns after Save policy. Unticking does not delete data or redact saved answers. Sensitive-looking columns are locked by name; review other fields yourself. Refresh keeps new columns disabled. Disabling join keys may prevent joins.

LangGraph runs guard -> retrieve -> generate -> validate -> execute -> respond. Errors route to retry -> generate, up to MAX_ATTEMPTS. Model context contains approved schema, not all rows; the optional evaluation sample profile includes approved example values. SQL validation and read-only execution are independent boundaries. Python summarizes returned rows and makes a bar chart when suitable. Success or recovery does not guarantee business correctness. Graph paths, SQL, assumptions and retries are inspectable. State is request-local; there is no conversation memory or automatic model training. Hosted tracing is disabled.

Defaults: MAX_ROWS=200, query timeout 5 seconds, three attempts, uploads 25 MB, SQL parsing 10 MB, imports 500,000 rows. Model calls have a separate timeout. Full schemas have a bounded context. Correct/Wrong ratings are user feedback, not benchmark accuracy; reviewed Wrong feedback can become regression cases through app.cli export-feedback/import-review.

## Harness evaluation

Each dataset gets schema-derived count, aggregate/grouping and refusal smoke checks. The optional ClassicModels pack contains 57 explicit cases: 8 lookup, 10 aggregation, 15 joins, 8 dates/filters, 6 explicit-interpretation ambiguous and 10 adversarial. All 51 answer references were executed on the sample data. Independent checks matched payments 8,853,839.23, order-line sales 9,604,190.61, 122 customers, 110 products and 326 orders.

Reference review: backend/evals/classicmodels-review.md. Ambiguous/join cases appear first. Status is agent-reviewed; human approval pending. The user will personally approve later. No model-judge score or human sign-off is fabricated.

Four core profiles compare Qwen 1.5B/3B at one attempt and configured retries. Optional ablations add full schema, validator-off and sample values. Validator-off can run only on private frozen read-only snapshots. Every run freezes approved data and records fingerprints. MySQL evaluation uses a converted SQLite representation, not live MySQL dialect performance.

Expected results come from executing reference SQL, independently of the model. The scorer compares returned rows, allows column aliases, ignores row order, preserves duplicates/associations and rounds numbers to two decimal places within MAX_ROWS. Invalid references are excluded. Required assumptions use phrase matching. Answer accuracy, safety and overall scores remain separate; tiers show sample counts. Failure labels are heuristics and can be manually overridden without changing pass/fail.

Repeats record temperature/seed, mean/spread and all/some/no-pass consistency. Warm-up is excluded. Answer/refusal timing is separate; p95 is suppressed below 30 samples. Repeats are correlated. Safety checks isolate guard, validator, database and injected sample values. Reports/exported JSON persist in storage/evaluations. Choose baselines and compare runs; altered data/suite/configuration yields exploratory comparisons instead of regression verdicts.

## Staged ClassicModels protocol

43 curated development cases plus 6 generated checks; 14 fixed held-out cases excluded from default runs. Reference review is allowed, but held-out model outputs must not guide tuning. A held-out check is reserved once per semantic split.

From backend, the first stage only:

```powershell
.\.venv\Scripts\python.exe -m evals.staged --dataset-id YOUR_DATASET_ID --stop-after core_once
```

Later, manually continue with the same command without --stop-after. Completed stages are reused. Stages: core_once (196 executions), core_three (588), fixed_three (588), heldout_once (56). A single predeclared fix is selected using development failure patterns. Comparisons and the failure story are written into the results section below. Improvements are not assumed; interrupted stages require attention. The interactive agent remains original until a fix is deliberately adopted. Keep the laptop awake and plugged in; close heavy applications yourself. No overnight scheduling was approved.

CI comparison, from backend:

```powershell
.\.venv\Scripts\python.exe -m evals.check --baseline baseline.json --current current.json --margin 0.05
```

Exit codes: 0 no measured regression, 1 regression, 2 invalid/incomparable input.

## Privacy and backup

Saved connection details are encrypted; uploaded data, history and audit/report files are not encrypted by the app. The encryption key is on the same computer. Protect backend/storage with filesystem permissions and disk encryption. Stop the application before backing up the entire storage folder, including credentials.key. No automatic retention cleanup is implemented. Downloads require internet during setup; inference and dataset operations stay local afterward.

This is a local single-user application, without enterprise RBAC, public HTTPS hosting or distributed workers. Do not expose the port publicly. API specification is available at /openapi.json.

<!-- STAGED_RESULTS_START -->

## Staged ClassicModels experiment

43 curated development cases + 6 generated checks; 14 held-out cases. References are agent-reviewed; human approval is pending.

Current stage: needs_attention. This section is generated from saved reports, not estimated scores.

| Stage | Profile | Answer accuracy | Answer n | Safety | Top failure |
| --- | --- | --- | ---: | --- | --- |

Held-out results are reported once and must not be used for further tuning. A failure is recorded as a failure; improvement is not assumed.

<!-- STAGED_RESULTS_END -->
