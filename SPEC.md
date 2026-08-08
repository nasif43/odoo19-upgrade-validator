# OUV — Odoo 18 → 19 Upgrade Validator
## Living Specification (`SPEC.md`)

> **This document reflects the system as it actually exists today.**
> It supersedes `initial_spec.md` for all active development and agent context.
> Where this document contradicts `initial_spec.md`, this document wins.

---

## 1. What This Tool Does

`ouv` is a deterministic, pipeline-based CLI tool that validates whether Odoo 18 custom modules are ready to be upgraded to Odoo 19. It is designed to be called by autonomous fixer agents with no developer in the loop per module. It emits a verdict and a structured report for every module it processes.

### 1.1 Verdict States

| Verdict | Meaning |
|---|---|
| `passed` | All static rules clean, dynamic install succeeded (or skipped) |
| `failed` | One or more blocking static rules fired, or dynamic install failed |
| `errored` | Unexpected exception during validation pipeline |
| `blocked` | Circuit breaker tripped, dispatch lock refused, or EE dep detected on CE install |

---

## 2. Architecture

### 2.1 Module Map

```
src/ouv/
├── cli.py            — Typer CLI entrypoint (4 commands: validate, history, ruleset-review, triage-clear)
├── orchestrator.py   — Central validation pipeline, circuit breaker, learning trigger
├── discovery.py      — Scans filesystem for Odoo modules with __manifest__.py
├── graph.py          — networkx DAG builder with manifestoo-core CE/EE classification
├── static_checks.py  — Regex + AST rule engine against ruleset YAML
├── installer.py      — odoo-bin subprocess installer → InstallResult
├── db_templates.py   — Postgres scratch DB lifecycle (clone template, drop)
├── verdict.py        — VerdictAggregator — combines static + dynamic results
├── reporting.py      — Writes report.json + findings.txt to output folder
├── history.py        — SQLite telemetry store (4 tables) + lineage query
├── dispatch_lock.py  — SQLite-backed fixer-agent concurrency lock
└── learning.py       — Passive learning event recorder + pending_updates.yaml queue
```

### 2.2 Data Flow

```
CLI validate
    │
    ├─ ModuleDiscoverer.discover_all(paths)
    │       └─ returns List[ModuleInfo]
    │
    ├─ DependencyGraphBuilder(modules)
    │       ├─ builds networkx DiGraph
    │       ├─ classifies each dep: internal / core_ce / core_ee / unknown_external
    │       └─ get_topological_order() → validation sequence
    │
    └─ for each module in topo order:
            Orchestrator.validate_module(module_info, agent_id)
                ├─ Git commit SHA capture
                ├─ Circuit breaker check (fix_attempts table)
                ├─ Dispatch lock acquire (dispatch_locks table)
                ├─ StaticAnalysisEngine.analyze_module()
                ├─ InstallExecutor.install_module() [if not dry-run]
                ├─ VerdictAggregator.aggregate()
                ├─ ReportGenerator.generate_report()
                ├─ HistoryStore.record_run()
                ├─ Circuit breaker update
                ├─ Learning engine trigger (if FAILED → PASSED transition)
                └─ Dispatch lock release
```

### 2.3 Dependency DAG & CE/EE Classification

`graph.py` uses **`manifestoo-core` v1.15.4** (ACSONE) for all CE/EE addon classification. Only `__all__`-exported symbols are used — no undocumented internals.

Every non-internal dependency gets one of four `kind` values stamped as a networkx node attribute:

| `kind` | Meaning | Source |
|---|---|---|
| `internal` | Present in the scanned module set | set membership |
| `core_ce` | Odoo 19.0 CE built-in (649 addons) | `is_core_ce_addon()` |
| `core_ee` | Odoo 19.0 EE built-in (772 addons) | `is_core_ee_addon()` |
| `unknown_external` | OCA / other custom / genuinely missing | fallthrough |

**New public methods on `DependencyGraphBuilder`:**
- `get_ee_dependencies(module_name)` — for `blocked` verdict detection on CE-only installs
- `get_unknown_external_dependencies(module_name)` — surfaces OCA/custom deps for human review

**Removed:** `external_addons: Optional[Set[str]]` constructor parameter — this was a caller-supplied set with no Odoo version awareness and defaulted to empty (silently missing all CE/EE addons). Replaced entirely by `manifestoo-core`.

---

## 3. State Storage

### 3.1 SQLite Schema (`history/index.sqlite`)

Four tables, all created by `HistoryStore._init_db()`:

#### `runs` — One row per validation attempt
| Column | Type | Description |
|---|---|---|
| `run_id` | TEXT PK | `run_<8hex>` |
| `module_name` | TEXT | Scanned module name |
| `verdict` | TEXT | `passed` / `failed` / `errored` / `blocked` |
| `timestamp` | TEXT | ISO-8601 UTC |
| `duration_ms` | INTEGER | Install duration (0 for dry-run/static-only) |
| `agent_id` | TEXT | Caller identity (`manual`, `ci`, or fixer agent ID) |
| `ruleset_version` | TEXT | Currently hardcoded `"1.0.0"` |
| `report_path` | TEXT | Absolute path to archived report folder |
| `fired_rule_ids` | TEXT | JSON array of rule IDs that matched |
| `install_error_sig` | TEXT | Fingerprint of install error (if any) |
| `prev_run_id` | TEXT | Linked previous run for lineage chain |
| `fix_agent_id` | TEXT | Non-null only for fixer-agent calls |
| `commit_sha` | TEXT | HEAD SHA at time of run |
| `fix_diff` | TEXT | Unified diff `HEAD~1..HEAD` at time of run |

#### `fix_attempts` — Circuit breaker state per module
| Column | Type | Description |
|---|---|---|
| `module_name` | TEXT PK | |
| `consecutive_failures` | INTEGER | Resets to 0 on `passed` |
| `last_attempt_run_id` | TEXT | |
| `status` | TEXT | `active` or `needs_human_triage` |

#### `dispatch_locks` — Fixer-agent concurrency
| Column | Type | Description |
|---|---|---|
| `module_name` | TEXT PK | |
| `status` | TEXT | `idle` or `locked` |
| `fix_agent_id` | TEXT | Holding agent |
| `claim_ts` | TEXT | ISO-8601 claim timestamp |
| `lock_timeout_s` | INTEGER | Default 1800s |

#### `learning_events` — Passive fix telemetry
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `timestamp` | TEXT | |
| `module_name` | TEXT | |
| `prev_run_id` | TEXT | |
| `curr_run_id` | TEXT | |
| `rules_resolved` | TEXT | JSON array |
| `error_fingerprint` | TEXT | |
| `confirmation_count` | INTEGER | Cross-module confirmation signal |
| `review_priority` | INTEGER | Sort hint for `ouv ruleset-review` |
| `ruleset_version_before` | TEXT | |
| `ruleset_version_after` | TEXT | |

### 3.2 Filesystem Layout

```
history/
├── index.sqlite              — All four SQLite tables
├── valid/<module>/<run_id>/  — Archived reports for passed runs
└── invalid/<module>/<run_id>/— Archived reports for failed/errored/blocked runs

validator_reports/            — Live (current run) report folders
└── <module_name>_validator_report/
    ├── report.json           — Machine-readable verdict, metadata, rule matches
    └── findings.txt          — Human-readable summary

ruleset/
├── odoo_18_19_rules.yaml     — Active ruleset (WRITE-PROTECTED — human gate only)
└── pending_updates.yaml      — Proposal queue written by learning engine
```

---

## 4. Autonomy Boundaries (Hard Rules — No Exceptions)

### 4.1 Ruleset Immutability
- **No code path may write to `ruleset/*.yaml` without a human explicitly running `ouv ruleset-review` and approving.**
- `confirmed_in_module_count`, `confirmed_fixes`, `install_error_signatures` — passive telemetry only.
- `OUV_AUTO_PROMOTE_THRESHOLD` — repurposed as a sort/priority hint for the review queue only. Never an auto-fire trigger.
- The learning engine writes **only** to `learning_events` (SQLite) and `pending_updates.yaml` (queue file). Never to `odoo_18_19_rules.yaml`.

### 4.2 Every Fix Attempt is a Git Commit
- Fixer-agent mode (`agent_id != "manual"` and `!= "ci"`) requires a git-managed repo.
- The `commit_sha` (HEAD at run time) and `fix_diff` (`HEAD~1..HEAD`) are captured on every run.
- Rollback is via `git revert <commit_sha>` — not via any OUV internal state reconstruction.

### 4.3 Circuit Breaker
- After `max_fix_attempts` (default 3, env `OUV_MAX_FIX_ATTEMPTS`) consecutive non-`passed` verdicts, the module's `fix_attempts.status` becomes `needs_human_triage`.
- A module in `needs_human_triage` refuses all fixer-agent dispatch immediately (returns `BLOCKED`).
- Reset only via `ouv triage-clear <module_name>`.

### 4.4 Dispatch Lock
- Only one fixer agent may hold the dispatch lock for a given module at a time.
- Lock is SQLite `BEGIN IMMEDIATE` with a 1800s timeout.
- Lock is always released in a `finally` block — no lock leaks on exceptions.

---

## 5. CLI Commands

```
ouv validate [PATHS]...
    --output-dir PATH      (default: ./validator_reports)
    --history-dir PATH     (default: ./history)
    --ruleset-path PATH    (default: ./ruleset/odoo_18_19_rules.yaml)
    --agent-id TEXT        (default: manual, env: OUV_AGENT_ID)
    --dry-run              Static analysis only, skip DB install
    --keep-db-on-failure   Don't drop scratch DB on failure
    --max-fix-attempts INT (default: 3, env: OUV_MAX_FIX_ATTEMPTS)

ouv history <MODULE_NAME>
    --history-dir PATH     Prints full attempt lineage chain

ouv ruleset-review
    --pending-updates-path PATH
    --ruleset-path PATH    Human-readable queue; shows proposals sorted by confirmation_count

ouv triage-clear <MODULE_NAME>
    --history-dir PATH     Resets needs_human_triage → active, zeroes consecutive_failures
```

---

## 6. Ruleset (`ruleset/odoo_18_19_rules.yaml`)

The active ruleset uses **schema version 2**. Each rule has:

| Field | Description |
|---|---|
| `id` | Unique rule identifier |
| `category` | `manifest` / `python` / `xml` / `security` |
| `severity` | `blocking` (causes `failed`) or `warning` (advisory only) |
| `evidence` | `OFFICIAL` / `COMMUNITY` |
| `check_type` | `regex` / `ast_check` / `xpath` |
| `applies_to` | Glob pattern for file targeting |
| `fix_hint` | Human/agent-readable remediation hint |

The ruleset currently ships rules for:
- **Manifest**: version string format, deprecated keys, dependency checks
- **Python**: deprecated API patterns (`openerp`, old field syntax, removed methods)
- **XML**: deprecated view attributes, old report tags, QWeb namespace changes
- **Security**: `ir.model.access.csv` format, security group references

---

## 7. Runtime Dependencies

| Package | Version | Role |
|---|---|---|
| `typer` | ≥0.9.0 | CLI framework |
| `rich` | ≥13.0.0 | Terminal output tables/colors |
| `networkx` | ≥3.0 | DAG construction, topological sort, cycle detection |
| `lxml` | ≥4.9.0 | XPath XML validation |
| `pydantic` | ≥2.0.0 | `report.json` schema validation |
| `ruamel.yaml` | ≥0.17.0 | Comment-preserving YAML for ruleset files |
| `psycopg2-binary` | ≥2.9.0 | Postgres scratch DB management |
| `manifestoo-core` | ≥1.0.0 | Odoo 19.0 CE/EE addon classification (bundled lists) |

Python ≥ 3.10 required.

---

## 8. Test Coverage

Four test files, **19 tests total**, all passing:

| File | Tests | What it covers |
|---|---|---|
| `test_ouv.py` | 4 | Discovery, DAG sort, static analysis, dispatch lock |
| `test_ouv_std.py` | 3 | Same core paths, `unittest.TestCase` style |
| `test_circuit_breaker.py` | 1 | Circuit breaker state machine |
| `test_graph_classification.py` | 11 | CE/EE/internal/unknown dep classification, `get_ee_dependencies`, `get_unknown_external_dependencies`, topo sort + cycle detection + layers |

Run with:
```bash
.venv/bin/pytest tests/ -v
```

---

## 9. What Is NOT Yet Built

The following are defined in `initial_spec.md` but **not yet implemented**:

- **Integration test tier against real Postgres + Odoo 19** — `db_templates.py` and `installer.py` exist but have not been validated against a live Odoo 19 instance.
- **`ruleset-review` approval write path** — the command shows the queue but does not yet write approved proposals into `odoo_18_19_rules.yaml`. Currently the human must edit the file manually after reviewing the queue output.
- **`OUV_EDITION`** env var for CE vs EE mode — `get_ee_dependencies()` exists on the graph builder but the orchestrator does not yet call it to gate the `blocked` verdict automatically.
- **Batch/parallel validation mode** — currently sequential over topological order.
- **`ouv rollback`** command — described in the initial spec but not wired into the CLI.

---

## 10. Key Divergences from `initial_spec.md`

| Area | initial_spec.md | This document (actual) |
|---|---|---|
| Dep classification | Caller-supplied `external_addons: Set[str]` | `manifestoo-core` CE/EE classification (19.0 bundled lists) |
| New graph methods | `get_missing_dependencies()` | `get_ee_dependencies()` + `get_unknown_external_dependencies()` |
| `OUV_AUTO_PROMOTE_THRESHOLD` | Auto-promotion trigger | Sort/priority hint only for review queue |
| Ruleset write path | Partially described as automatic | Hard gate: human must run `ouv ruleset-review` and manually approve |
| `ruleset-review` | Described as interactive approval | Currently read-only display queue; write path not yet wired |
