# System Map — Odoo 18 → 19 Upgrade Validator (`ouv`)

## Architecture Overview

`ouv` is a CLI tool and python framework designed to discover Odoo modules, build a dependency DAG, execute static analysis against version upgrade rules, run isolated dynamic installation checks using Postgres template DBs, store run history & telemetry, and manage fixer-agent autonomy boundaries.

```
┌─────────────────────────────────────────────────────────────┐
│                       CLI (`ouv`)                           │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                    Orchestrator (`ouv.orchestrator`)        │
└──────┬────────────┬─────────────┬──────────────┬─────────────┘
       │            │             │              │
┌──────▼──────┐ ┌───▼───────┐ ┌───▼────────┐ ┌───▼───────────┐ ┌──────────────┐
│ Discoverer  │ │ Graph     │ │ Static     │ │ DB Template   │ │ Installer    │
│ (discovery) │ │ Builder   │ │ Analysis   │ │ Manager       │ │ Executor     │
│             │ │ (graph)   │ │ (static)   │ │ (db_templates)│ │ (installer)  │
└─────────────┘ └───────────┘ └────────────┘ └───────────────┘ └──────┬───────┘
                                                                      │
┌─────────────────────────────────────────────────────────────────────▼┐
│                        Verdict Aggregator                            │
│                       (`ouv.verdict`)                                │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │
                ┌──────────────────┴──────────────────┐
                ▼                                     ▼
┌───────────────────────────────┐     ┌────────────────────────────────┐
│       Report Generator        │     │     History & Telemetry        │
│       (`ouv.reporting`)       │     │     Store (`ouv.history`)      │
└───────────────────────────────┘     └───────────────┬────────────────┘
                                                      │
                                      ┌───────────────┴────────────────┐
                                      ▼                                ▼
                      ┌──────────────────────────────┐ ┌───────────────┐
                      │  Ruleset Learning & Review   │ │ Dispatch Lock │
                      │  (`ouv.learning`)            │ │ (dispatch_lock│
                      └──────────────────────────────┘ └───────────────┘
```

## Component Directory Map

| Component / Module | Responsibility | Key Dependencies | Spec Section |
|-------------------|----------------|------------------|--------------|
| `src/ouv/discovery.py` | Recursively discover Odoo modules & parse manifests safely via AST | `ast` | §4.1 |
| `src/ouv/graph.py` | Build DAG, detect cycles, topological sorting, dependency resolution | `networkx` | §4.2 |
| `src/ouv/static_checks.py` | Generic static analysis rule engine (regex, AST, XPath, callable) | `ruleset/*.yaml`, `lxml`, `ast` | §4.3 |
| `src/ouv/db_templates.py` | Postgres golden base & scratch template cloning/purging | `psycopg2` / `psycopg` | §4.4 |
| `src/ouv/installer.py` | Subprocess execution pool running `odoo-bin --stop-after-init` | `subprocess`, `concurrent.futures` | §4.5 |
| `src/ouv/verdict.py` | Deterministic verdict aggregation (`passed`/`failed`/`errored`/`blocked`) | - | §4.6 |
| `src/ouv/reporting.py` | Generate `<module>_validator_report/` (report.json, install.log, README.md) | `pydantic` | §4.7 |
| `src/ouv/history.py` | SQLite store (`history/index.sqlite`) & filesystem run archive | `sqlite3` | §4.8 |
| `src/ouv/learning.py` | Passive learning queue & `ouv ruleset review` human update pipeline | `ruamel.yaml` / `pyyaml` | §4.9 |
| `src/ouv/dispatch_lock.py` | SQLite-backed concurrency locking for parallel fixer agents | `sqlite3` | §4.11 |
| `src/ouv/orchestrator.py` | Core pipeline workflow, retry circuit breaker, dispatch locks | All components | §4 & Addendum v1.2 |
| `src/ouv/cli.py` | Typer CLI entrypoint (`ouv validate`, `ouv history lineage`, etc.) | `typer`, `rich` | §4 & §9 |

## Database Schema (`history/index.sqlite`)

- **`runs`**: Attempt history per run (includes `run_id`, `module_name`, `verdict`, `commit_sha`, `fix_diff`, `fix_agent_id`, `prev_run_id`, `fired_rule_ids`, `install_error_sig`, `duration_ms`, `timestamp`)
- **`fix_attempts`**: Circuit breaker tracking (`module_name`, `consecutive_failures`, `last_attempt_run_id`, `status`)
- **`dispatch_locks`**: Concurrent agent locking (`module_name`, `status`, `fix_agent_id`, `claim_ts`, `lock_timeout_s`)
- **`learning_events`**: Feedback loop event log (`id`, `module_name`, `prev_run_id`, `curr_run_id`, `rules_resolved`, `confirmation_count`, `review_priority`, `ruleset_version_before`, `ruleset_version_after`)
