# Architectural Decisions Log (`DECISIONS.md`)

## Design Principles & Architectural Decisions

### 1. Zero Runtime LLM Verdict Generation
- **Decision:** Every verdict (`passed`, `failed`, `errored`, `blocked`) is computed strictly deterministically using static rule patterns or `odoo-bin` process exit codes.
- **Rationale:** Prevents nondeterministic behavior and token burn during recurring runs across 127+ custom modules.

### 2. Strict Ruleset Immutability & Human-Gated Updates (Addendum v1.2)
- **Decision:** No automated or programmatic code path may edit `ruleset/*.yaml`.
- **Rationale:** Agent-driven confirmations can be correlated rather than independent. All telemetry (`confirmed_in_module_count`, `confirmed_fixes`) is passive. Auto-promotion threshold (`OUV_AUTO_PROMOTE_THRESHOLD`) acts solely as a priority/sort hint for `ouv ruleset review`.

### 3. Git-Authoritative Rollback & Diff Lineage
- **Decision:** Rollbacks must use `git revert <sha>` based on `commit_sha` logged per run in `report.json` and SQLite. Diffs are captured as plain unified diff text per run.
- **Rationale:** Prevents state drift and fragile JSON-reconstructed file mutations. Makes fixer-agent changes fully audit-able.

### 4. Circuit Breaker & Concurrency Locking in SQLite
- **Decision:** Use stdlib `sqlite3` with `BEGIN IMMEDIATE` transactions to handle `dispatch_locks` and `fix_attempts` circuit breaker tracking.
- **Rationale:** Avoids introducing external infrastructure dependencies like Redis or Zookeeper while ensuring safety across parallel fixer agents.

### 5. Dependency Choices
- `typer` / `click` for CLI ergonomics.
- `networkx` for topological sort & dependency DAG cycle detection.
- `lxml` for XPath view XML validation.
- stdlib `ast` for Python static rule checks.
- `pydantic` for `report.json` contract validation.
- `ruamel.yaml` for comment-preserving YAML reading/writing during `ouv ruleset review`.
