# OUV — Odoo 18 → 19 Upgrade Validator

A deterministic, pipeline-based CLI validator for Odoo custom module upgrades from v18 to v19. Designed to be called by autonomous fixer agents with no developer in the loop per module.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | ≥ 3.10 |
| PostgreSQL | ≥ 14 (only needed for dynamic install checks) |
| Odoo 19 | Must be installed and `odoo-bin` on PATH (dynamic checks only) |
| Git | Any recent version |

---

## Quick Start (Static Analysis Only)

If you just want to run static rule checks against your modules without a live Odoo/Postgres setup:

```bash
# 1. Clone the repo
git clone git@github.com:nasif43/odoo19-upgrade-validator.git
cd odoo19-upgrade-validator

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install ouv and all dependencies
pip install -e .

# 4. Run static-only validation against your modules (dry-run skips DB install)
ouv validate /path/to/your/odoo/addons --dry-run
```

Reports land in `./validator_reports/`. History is written to `./history/`.

---

## Full Setup (Static + Dynamic Install Checks)

The dynamic tier actually installs each module into a scratch Postgres DB cloned from a template. This requires a running Odoo 19 instance and Postgres access.

### Step 1 — Install System Prerequisites

```bash
# Debian/Ubuntu
sudo apt-get install python3-dev libpq-dev postgresql postgresql-client git

# macOS (Homebrew)
brew install postgresql git
```

### Step 2 — Set Up Postgres

```bash
# Start postgres if not already running
sudo systemctl start postgresql      # Linux
brew services start postgresql       # macOS

# Create a superuser for ouv (or use an existing one)
sudo -u postgres createuser --superuser $USER

# Create the Odoo 19 template database (install Odoo 19 modules into this once)
createdb odoo19_template
# Then install Odoo 19 base into it via odoo-bin before using ouv
```

### Step 3 — Install OUV

```bash
git clone git@github.com:nasif43/odoo19-upgrade-validator.git
cd odoo19-upgrade-validator

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
```

### Step 4 — Configure Odoo Connection

Create an Odoo config file (e.g. `odoo.conf`) pointing at your Odoo 19 install:

```ini
[options]
addons_path = /path/to/odoo19/addons,/path/to/your/custom/addons
db_host = localhost
db_port = 5432
db_user = your_pg_user
db_password = your_pg_password
```

### Step 5 — Run Full Validation

```bash
ouv validate /path/to/your/custom/addons \
    --odoo-config /path/to/odoo.conf \
    --output-dir ./reports \
    --history-dir ./history
```

---

## CLI Reference

```
ouv validate [PATHS]...
    --output-dir PATH         Report output directory (default: ./validator_reports)
    --history-dir PATH        History & SQLite database (default: ./history)
    --ruleset-path PATH       Ruleset YAML (default: ./ruleset/odoo_18_19_rules.yaml)
    --agent-id TEXT           Caller identity, env: OUV_AGENT_ID (default: manual)
    --dry-run                 Static analysis only — skip Postgres/Odoo install check
    --keep-db-on-failure      Don't drop scratch DB on failure (for debugging)
    --max-fix-attempts INT    Circuit breaker threshold, env: OUV_MAX_FIX_ATTEMPTS (default: 3)

ouv history <MODULE_NAME>
    --history-dir PATH        Print full attempt lineage chain for a module

ouv ruleset-review
    --pending-updates-path PATH   Show human review queue (proposals sorted by confirmation count)
    --ruleset-path PATH

ouv triage-clear <MODULE_NAME>
    --history-dir PATH        Reset needs_human_triage → active, reset failure counter
```

---

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=ouv --cov-report=term-missing
```

Expected output: **19 tests, all passing**.

---

## Understanding Verdicts

| Verdict | Meaning |
|---|---|
| `PASSED` | All static rules clean; install succeeded (or dry-run) |
| `FAILED` | Blocking rule(s) fired, or Odoo install failed |
| `ERRORED` | Unexpected exception during pipeline |
| `BLOCKED` | Circuit breaker tripped, lock refused, or Enterprise Edition dependency detected on CE install |

---

## Autonomy Safety Rules (Read Before Using with Agents)

1. **Ruleset is write-protected.** `ruleset/odoo_18_19_rules.yaml` is never modified by `ouv` itself. Proposed updates land in `ruleset/pending_updates.yaml` for human review via `ouv ruleset-review`.

2. **Every fixer-agent fix must be a git commit.** Running as a fixer agent (`--agent-id` set to anything other than `manual` or `ci`) requires a git-managed repo. The commit SHA and diff are recorded per run.

3. **Circuit breaker.** After `--max-fix-attempts` consecutive non-passing results, a module is locked from further fixer-agent dispatch. Clear manually with `ouv triage-clear <module>`.

4. **Dispatch lock.** Only one fixer agent can validate a module at a time. Concurrent agents are refused immediately.

---

## Project Layout

```
odoo19-upgrade-validator/
├── src/ouv/              — All source modules
│   ├── cli.py            — CLI entrypoint
│   ├── orchestrator.py   — Validation pipeline
│   ├── discovery.py      — Module filesystem scanner
│   ├── graph.py          — Dependency DAG + CE/EE classification
│   ├── static_checks.py  — Regex/AST rule engine
│   ├── installer.py      — odoo-bin subprocess runner
│   ├── db_templates.py   — Postgres scratch DB lifecycle
│   ├── verdict.py        — Verdict aggregation
│   ├── reporting.py      — report.json + findings.txt writer
│   ├── history.py        — SQLite telemetry + lineage
│   ├── dispatch_lock.py  — Concurrency lock
│   └── learning.py       — Passive fix telemetry + review queue
├── ruleset/
│   └── odoo_18_19_rules.yaml  — Active ruleset (human-gated)
├── tests/                — 19 unit tests
├── SPEC.md               — Living specification (source of truth)
├── DECISIONS.md          — Architectural decision log
├── SYSTEM_MAP.md         — System component map
├── initial_spec.md       — Original design spec (historical reference only)
└── pyproject.toml        — Package definition and dependencies
```

---

## Key Dependencies

| Package | Purpose |
|---|---|
| `networkx` | Dependency DAG, topological sort, cycle detection |
| `manifestoo-core` | Odoo 19.0 CE/EE addon classification (649 CE + 772 EE addons bundled) |
| `typer` + `rich` | CLI and terminal output |
| `lxml` | XPath XML rule checks |
| `pydantic` | Report JSON schema |
| `ruamel.yaml` | Comment-preserving YAML for ruleset files |
| `psycopg2-binary` | Postgres scratch DB management |

---

## Further Reading

- **`SPEC.md`** — Living specification: actual architecture, schema, what's built, what's not
- **`DECISIONS.md`** — Why specific design choices were made
- **`SYSTEM_MAP.md`** — Component-level system map
- **`initial_spec.md`** — Original design document (historical reference; may conflict with current build)
