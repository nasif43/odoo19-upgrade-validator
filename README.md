# Odoo 18 → 19 Upgrade Validator (`ouv`)

`ouv` is an internal CLI tool and Python framework designed to discover Odoo custom modules, validate their 18 → 19 upgrade readiness through static analysis rules and isolated Postgres database installation checks, maintain telemetry/lineage history, and enforce autonomy boundaries for autonomous fixer agents.

## Quick Start

### 1. Prerequisites
- Python 3.10+
- PostgreSQL server running locally (or remote host configured via env vars)
- `odoo-bin` executable available on `$PATH` or specified via CLI options

### 2. Installation
Set up a virtual environment and install the package in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

### 3. Usage

#### Validate Custom Modules
Validate upgrade readiness for one or more module directories:

```bash
ouv validate /path/to/custom_modules --dry-run
```

Available flags:
- `--output-dir`: Folder for generated validation reports (default: `./validator_reports`)
- `--history-dir`: Folder for SQLite index and run history archives (default: `./history`)
- `--ruleset-path`: Path to ruleset YAML file (default: `./ruleset/odoo_18_19_rules.yaml`)
- `--agent-id`: Caller or fixer agent identity (env: `OUV_AGENT_ID`, default: `manual`)
- `--dry-run`: Run static rules only, skip Postgres installation attempt
- `--keep-db-on-failure`: Retain scratch DB on failure for debugging

#### Query Module Lineage
Display full attempt history and diffs for a module (§4.12):

```bash
ouv history <module_name>
```

#### Review Pending Ruleset Update Proposals
Interactively review passive feedback proposals enqueued by fixer agents:

```bash
ouv ruleset-review
```

#### Clear Circuit Breaker Triage Status
Reset `needs_human_triage` status for a module:

```bash
ouv triage-clear <module_name>
```

### 4. Running Tests
Run the test suite using `pytest`:

```bash
pytest tests/
```
