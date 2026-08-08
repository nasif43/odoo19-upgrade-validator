# Odoo 18 → 19 Upgrade Validator — Technical Specification

**Status:** Ruleset research complete — ready for implementation
**Target implementer:** Antigravity IDE (Gemini 3.6 Low)
**Author context:** Internal tool, Pridesys IT — validates ~127 custom modules being migrated 18→19
**Spec version:** 1.3 (Addendum v1.2 — Autonomy Boundaries for Fixer-Agent Handoff integrated)

---

## 0. Read This First (instructions for the implementing agent)

You (the coding agent) are implementing this spec. Follow these constraints strictly:

1. **No LLM in the pass/fail path.** Every verdict (`passed` / `failed` / `errored` / `blocked`) must be produced by deterministic code — static analysis rules, subprocess exit codes, database state checks. LLMs may only be used to *generate human-readable explanations* of a verdict that has already been computed deterministically, never to compute the verdict itself.
2. **Do not re-derive Odoo 18→19 breaking changes from your own knowledge or by re-fetching docs on every run.** Section 3 defines a persistent ruleset artifact (`ruleset/odoo_18_19_rules.yaml`). Build it once, carefully, from official Odoo documentation (odoo.com/documentation/19.0, official changelog, official upgrade util if referenced). After it exists, the validator reads only that file — it does not call you or any LLM at validation time.
3. **This is not a one-shot script.** Structure this as an installable Python package (`src/` layout) with a clean internal API, not a single monolithic file. Every component in Section 2 should be independently testable and independently replaceable.
4. **Use existing libraries for solved problems. Do not write your own DAG algorithm, YAML parser, or subprocess pool.** See Section 6 for the approved library list and the reasoning per choice. If you believe a library outside this list is warranted, note it in `DECISIONS.md` with justification — do not silently add dependencies.
5. **Write tests as you go**, not at the end. Each component in Section 2 has a "testability" note — respect it.
6. **When you are uncertain about an actual Odoo 19 API/behavior detail** (not covered in this spec or the ruleset file), do not guess and silently proceed. Flag it in `DECISIONS.md` under an "Open Questions" heading and use the most conservative interpretation (i.e., prefer `blocked` verdict over a false `passed`).
7. Produce a `SYSTEM_MAP.md` and `DECISIONS.md` at the repo root as you build, per standard practice — living documents, not end-of-project writeups.

---

## 1. Goals & Non-Goals

### Goals
- Given one or more folders of Odoo module source, recursively discover modules, build a dependency graph, and validate each module's 18→19 upgrade readiness.
- Validation = static rule checks (fast, deterministic, no DB) followed by a real install attempt (dynamic, ground-truth) against a local Odoo 19 instance's database layer — never against the live/persistent server instance.
- Produce a self-contained `<module_name>_validator_report/` folder per module with a canonical JSON report and a derived human-readable summary.
- Maintain a `history/valid/` and `history/invalid/` archive with run telemetry (agent identity, run count, duration, verdict trend) to improve agentic workflows over time.
- Package as an internal CLI tool: `ouv validate <path> [options]` (name placeholder — see Section 9).
- Be callable by Antigravity or any other program without requiring an LLM at runtime.

### Non-Goals (this phase)
- No UI/video evidence capture (explicitly deferred — see Section 10, Phase 3).
- No modification of the persistent/server Odoo 19 instance — read-only with respect to it (it's only a source of binaries/addons-path, never a target).
- No support for validating against Odoo versions other than 18→19 in this phase (but architecture must not preclude it — see Section 8).
- No web UI / dashboard in this phase (telemetry is queryable via SQLite directly or a later CLI report command).

---

## 2. Architecture Overview

```
                         ┌─────────────────────────┐
                         │   CLI (ouv validate)     │
                         └────────────┬─────────────┘
                                      │
                         ┌────────────▼─────────────┐
                         │   Orchestrator (core)     │
                         └────────────┬─────────────┘
                                      │
        ┌───────────────┬────────────┼────────────┬──────────────────┐
        ▼               ▼            ▼             ▼                  ▼
┌───────────────┐ ┌───────────┐ ┌──────────┐ ┌────────────┐  ┌────────────────┐
│ Module         │ │ Dependency│ │ Static   │ │ DB Template│  │ Install         │
│ Discoverer     │ │ Graph     │ │ Analysis │ │ Manager    │  │ Executor Pool   │
│                │ │ Builder   │ │ Engine   │ │ (Postgres) │  │ (subprocess)    │
└───────────────┘ └───────────┘ └────┬─────┘ └─────┬──────┘  └────────┬────────┘
                                      │              │                  │
                                      └──────┬───────┴──────────────────┘
                                             ▼
                                  ┌───────────────────┐
                                  │  Verdict Aggregator │
                                  └─────────┬───────────┘
                                            │
                          ┌─────────────────┼──────────────────┐
                          ▼                                    ▼
                ┌───────────────────┐                ┌──────────────────────┐
                │ Report Generator   │                │ History/Telemetry    │
                │ (per-module folder)│                │ Store (SQLite + tree)│
                └───────────────────┘                └──────────────────────┘
```

Everything reads from **one persistent artifact you don't regenerate per run**: `ruleset/odoo_18_19_rules.yaml` (Section 3).

---

## 3. The Ruleset Knowledge Base (the persistent document)

This is the component that solves the "don't burn tokens re-deriving the same facts every run" problem. Build it once, review it like code, version it.

### 3.1 Construction process (completed — AI research pass)

The ruleset has been constructed by an AI research pass (August 2026) using the following
evidence-tiering methodology:

1. **OFFICIAL** — Confirmed in `odoo.com/documentation/19.0` or a verified merged
   `github.com/odoo/odoo` PR.  Highest confidence.  Used as-is.

2. **COMMUNITY** — Not in official docs, but confirmed by **3 or more independent
   2025 sources** (solved Odoo forum threads, accepted reddit.com/r/Odoo answers,
   dev.to/medium/cybrosys blog posts, wcblog.in, winotto.com).  Only issues with
   **resolved/accepted** status counted — open/disputed issues were excluded.
   Confidence: high, used as rules but the engine should surface the evidence tier
   in reports so developers can see which rules have primary-source backing.

3. **ACKNOWLEDGED-UNVERIFIED** — Raised in the community but either disputed, only
   one source, or still in open (unsolved) issues.  These are listed in the
   `ACKNOWLEDGED_UNVERIFIED` section of the YAML file — **not as rules**.  They
   should be checked during QA and promoted to rules when confirmed.

The evidence tier per rule is stored in the `evidence` field of each rule entry.
The static analysis engine MUST pass this field through to the report — it is
part of the per-rule result in `report.json` and in the human-readable `README.md`.

File is committed to version control.  All future validator runs read this file only.
No re-fetching of documentation at runtime.

### 3.2 Schema

```yaml
# ruleset/odoo_18_19_rules.yaml
schema_version: 2                     # bumped to 2 to include learning fields
target_migration: "18.0->19.0"
sources:
  - "https://www.odoo.com/documentation/19.0/..."  # list actual sources used
generated: "2026-08-08"
reviewed_by: "AI-research-pass-1"     # or null if not yet reviewed
ruleset_version: "1.0.0"              # semver — learning engine bumps patch on update

rules:
  - id: MANIFEST_VERSION_STRING
    category: manifest              # manifest | python_api | view_xml | security_xml
                                    # controller | scss | qweb
    severity: blocking              # blocking | warning | info
    evidence: OFFICIAL              # OFFICIAL | COMMUNITY  (always required)
    description: "__manifest__.py 'version' key must start with 19.0"
    check_type: regex               # regex | ast | xpath | callable
    pattern: '^"version":\s*"19\.0\.'
    applies_to: "**/__manifest__.py"
    fix_hint: "Update version string to 19.0.x.y.z"
    source: "https://www.odoo.com/documentation/19.0/developer/reference/module.html"
    # Learning loop fields (populated automatically — do not edit by hand):
    confirmed_in_module_count: 0    # incremented each time a fix for this rule is confirmed
    confirmed_fixes: []             # list of {module, before_snippet, after_snippet, run_id}
    install_error_signatures: []    # error fingerprints seen when this rule fired (dynamic phase)

  - id: ORM_OSV_IMPORT
    category: python_api
    severity: blocking
    evidence: OFFICIAL
    description: "odoo.osv deprecated in 19.0 (PR #217708). Use odoo.fields.Domain."
    check_type: ast
    ast_pattern: "import_from_module starts_with 'odoo.osv'"
    applies_to: "**/*.py"
    fix_hint: "Replace expression.AND/OR with Domain() & / | operators"
    source: "github.com/odoo/odoo/pull/217708"

  - id: ORM_API_RETURNS_REMOVED
    category: python_api
    severity: blocking
    evidence: COMMUNITY
    description: "@api.returns decorator removed in Odoo 19. Simply remove it."
    check_type: ast
    ast_pattern: "decorator_name == 'api.returns'"
    applies_to: "**/*.py"
    fix_hint: "Remove @api.returns decorator entirely."
    source: "Community: reddit.com/r/Odoo (solved 2025), cybrosys.com"

  - id: ORM_SQL_CONSTRAINTS_REMOVED
    category: python_api
    severity: blocking
    evidence: COMMUNITY
    description: "_sql_constraints silently ignored in Odoo 19. Use models.Constraint."
    check_type: ast
    ast_pattern: "attribute_assignment_name == '_sql_constraints'"
    applies_to: "**/*.py"
    fix_hint: "Replace list with individual models.Constraint class attributes."
    source: "Community: odoo.com forum (solved 2025), cybrosys.com, codeerts.com"

  - id: SECURITY_GROUPS_ID_RENAMED_PY
    category: python_api
    severity: blocking
    evidence: COMMUNITY
    description: "res.users.groups_id renamed to group_ids. res.groups.users renamed to user_ids."
    check_type: regex
    pattern: "\\.groups_id\\b"
    applies_to: "**/*.py"
    fix_hint: "Replace .groups_id with .group_ids. Replace group.users with group.user_ids."
    source: "Community: odoo.com forum (solved 2025), winotto.com, cybrosys.com"

  - id: SECURITY_REIFIED_GROUPS_REMOVED
    category: python_api
    severity: blocking
    evidence: COMMUNITY
    description: "sel_groups_* and in_group_* reified fields removed from res.users in Odoo 19."
    check_type: regex
    pattern: "\\b(sel_groups_|in_group_)\\w+"
    applies_to: "**/*.py"
    fix_hint: "Use user.has_group(xml_id) or user.group_ids membership checks."
    source: "Community: cybrosys.com, winotto.com, wcblog.in (2025)"

  - id: VIEW_ATTRS_REMOVED
    category: view_xml
    severity: blocking
    evidence: OFFICIAL
    description: "attrs= attribute removed from XML views in Odoo 17+, absent in Odoo 19."
    check_type: xpath
    xpath_pattern: "//*[@attrs]"
    applies_to: "**/*.xml"
    fix_hint: "Replace attrs= with direct invisible/readonly/required Python expressions."
    source: "https://www.odoo.com/documentation/19.0/developer/reference/user_interface/view_architectures.html"

  - id: VIEW_KANBAN_BOX_RENAMED
    category: view_xml
    severity: warning
    evidence: COMMUNITY
    description: "t-name='kanban-box' deprecated in Odoo 19. Use t-name='card'."
    check_type: xpath
    xpath_pattern: "//t[@t-name='kanban-box']"
    applies_to: "**/*.xml"
    fix_hint: "Rename <t t-name='kanban-box'> to <t t-name='card'>."
    source: "Community: wcblog.in, cybrosys.com, github.com/odoo/odoo issues (2025)"

  - id: SECURITY_GROUP_CATEGORY_ID_REPLACED
    category: security_xml
    severity: blocking
    evidence: COMMUNITY
    description: "res.groups.category_id replaced by privilege_id (res.groups.privilege model, Odoo 19)."
    check_type: xpath
    xpath_pattern: "//record[@model='res.groups']/field[@name='category_id']"
    applies_to: "**/security/*.xml"
    fix_hint: "Create res.groups.privilege record and reference via privilege_id."
    source: "Community: cybrosys.com, wcblog.in, winotto.com, odoo.com forum (2025)"

  - id: CONTROLLER_JSON_TYPE_RENAMED
    category: controller
    severity: warning
    evidence: OFFICIAL
    description: "HTTP route type='json' renamed to type='jsonrpc' in Odoo 18.1 (PR #183636)."
    check_type: regex
    pattern: "type\\s*=\\s*['\"]json['\"]"
    applies_to: "**/*.py"
    fix_hint: "Replace type='json' with type='jsonrpc' in @http.route() decorators."
    source: "github.com/odoo/odoo/pull/183636"

  # Full rule list: see ruleset/odoo_18_19_rules.yaml (27 rules across 7 categories)
```

**Rule categories in scope (as of research pass 1):**

| Category | Count | Notes |
|---|---|---|
| `manifest` | 3 | version string, license field, openerp.py |
| `python_api` | 12 | ORM methods, decorators, constraints, security field renames |
| `view_xml` | 4 | attrs, states, kanban-box, search group attrs |
| `security_xml` | 2 | category_id→privilege_id, groups_id→group_ids in XML |
| `controller` | 1 | type='json'→'jsonrpc' |
| `scss` | 1 | @import deprecated |
| `qweb` | 2 | t-call-assets filter attrs, inline script CSP |

Each rule is independently testable: a rule is just (pattern, applies_to, severity). The static analysis engine (Section 4.2) is a generic rule executor — it has **zero hardcoded Odoo 18/19 knowledge itself**. This is the key extensibility property: to support 19→20 later, you write a new YAML file, not new engine code.

### 3.3 Versioning
- File is namespaced by migration path (`odoo_18_19_rules.yaml`, later `odoo_19_20_rules.yaml`).
- `schema_version` field allows the engine to evolve the rule schema without breaking old rule files.
- Updates to the ruleset are a deliberate, reviewed act — not a side effect of a validator run.

---

## 4. Component Breakdown

### 4.1 Module Discoverer
- Input: one or more root folder paths.
- Recursively finds directories containing `__manifest__.py` (or legacy `__openerp__.py` — flag as `blocked` with a clear message, don't attempt to parse).
- Parses each manifest safely (use `ast.literal_eval`, **never** `eval()`, since manifests are technically Python dicts but should be treated as untrusted data).
- Output: list of `ModuleInfo` objects (name, path, manifest dict, declared depends).

**Testability:** pure function over a filesystem fixture — trivial to unit test with a tmp directory of fake modules.

### 4.2 Dependency Graph Builder
- Builds a DAG from `depends` keys across all discovered modules, using `networkx.DiGraph`.
- Detects cycles → hard error (Odoo itself would reject this; catch it before wasting install time).
- Produces topological order for validation scheduling — modules with satisfied dependencies validate first, so failures propagate correctly (a module `blocked` because a dependency failed shouldn't be reported as an unrelated install error).
- Identifies "layers" (dependency depth) — used by the DB Template Manager to decide template reuse boundaries (Section 4.4).
- Distinguishes internal deps (present in your scanned folder set) from external deps (core Odoo CE/EE addons, resolved against the reference Odoo 19 addons-path) — modules depending on unavailable/unlicensed EE addons get `blocked`, not `failed`.

**Testability:** `networkx` graph operations are well-tested upstream; your code here is graph *construction* from manifests, testable with fixture manifests.

### 4.3 Static Analysis Engine
- Generic rule executor, reads `ruleset/*.yaml`, has no embedded Odoo version knowledge.
- Supported check types:
  - `regex` — simple pattern match against file text (fast, good for manifest/version strings)
  - `ast` — Python `ast` module walk for deprecated method calls, removed imports (use stdlib `ast`, not a third-party Python parser — stdlib is sufficient for method/call detection and has zero extra dependency weight)
  - `xpath` — `lxml.etree` XPath queries against view/XML files (removed/renamed attributes, deprecated tags)
  - `callable` — escape hatch: a rule can reference a small Python function in `ruleset/custom_checks.py` for anything the declarative formats can't express
- Runs **before** any database/install work. Any `blocking` severity failure short-circuits that module straight to `failed` — never reaches the DB phase. This is the single biggest wall-clock saver at 127-module scale.
- `warning` severity rules are recorded but don't block install attempt.

**Testability:** each rule + a crafted "before" fixture file = one test case. This is naturally table-driven — write a test harness that runs the ruleset against a `tests/fixtures/static_checks/<rule_id>/` directory of pass/fail sample files.

### 4.4 Database Template Manager
- Manages Postgres template-DB cloning to avoid re-running a full base install per module.
- `golden_base`: one-time-built DB with Odoo 19 CE (+ EE if licensed) base modules installed. Built once per validator "session generation" (invalidate/rebuild if the reference Odoo 19 codebase changes — hash the addons-path or track an Odoo commit/version marker).
- Layered templates: for dependency layers identified in 4.2, optionally materialize intermediate templates (e.g., a template with the 5 most-depended-on internal modules pre-installed) to shortcut common chains. This is an optimization — ship a v1 without it, add it once you have real timing data showing it's worth the complexity (see Section 7 trade-offs).
- Use `CREATE DATABASE scratch_<uuid> WITH TEMPLATE golden_base` (or layered template) via `psycopg2`/`sqlalchemy` — filesystem-level copy in Postgres, seconds not minutes.
- Purges all scratch DBs at session end (`DROP DATABASE`) unless `--keep-db-on-failure` flag is passed (useful for manual debugging of a specific failure — see CLI options).

**Testability:** integration-test tier (needs real Postgres) — keep this isolated behind an interface so unit tests elsewhere can mock it.

### 4.5 Install Executor Pool
- Runs `odoo-bin -c <config> -d <scratch_db> -i <module> --stop-after-init --no-http --log-level=info` as a subprocess per module.
- **Never spins up a persistent Odoo web server for validation.** `--stop-after-init --no-http` gives you a clean process that installs and exits with a real exit code — this is your ground truth signal, no HTTP layer needed.
- Optional `--test-enable --test-tags /<module_name>` pass if the module ships Odoo unit tests — capture and parse Odoo's test-run summary from stdout/log.
- Concurrency: use `concurrent.futures.ProcessPoolExecutor` (or `asyncio.subprocess` if you prefer async — either is fine, don't build a custom pool) with a configurable `--max-parallel` (default: conservative, e.g. `min(cpu_count - 2, 4)` — leave headroom for Postgres itself, which is doing real IO work under concurrent template clones).
- Captures full stdout/stderr + exit code + wall-clock duration per attempt. This raw log is preserved verbatim in the report folder (Section 5) — don't summarize-and-discard, the raw log is often what a developer actually needs.
- Distinguishes exit reasons: clean failure with Odoo traceback (`failed`), process-level error like DB connection refused or timeout (`errored` — infrastructure problem, not a module problem, should be retried not reported as a module defect), missing dependency that graph builder already flagged (`blocked`, should have short-circuited earlier — if it reaches here, that's a graph-builder bug worth logging).

**Testability:** mock the subprocess boundary in unit tests (inject a fake `subprocess.run`); reserve real `odoo-bin` invocation for an integration test tier that requires a real Odoo 19 install (mark these tests explicitly, e.g. `pytest.mark.integration`, so CI/day-to-day dev runs stay fast).

### 4.6 Verdict Aggregator
- Combines static + dynamic results into the final four-state verdict per module.
- Verdict logic (explicit, not implicit):
  - `blocked`: unresolvable dependency, missing EE license, cyclic dependency, or upstream module in the same graph already `failed`/`blocked`
  - `failed`: static rule violation (blocking severity) OR dynamic install exited non-zero with an Odoo-level error
  - `errored`: infrastructure/tooling failure (DB connection issue, timeout, subprocess crash) — distinct from `failed` because it means "we don't know," not "it's broken"
  - `passed`: static checks clean (or warnings only) AND install exited 0 (AND tests passed, if run)

### 4.7 Report Generator
- Per module: `<module_name>_validator_report/`
  - `report.json` — canonical artifact. Schema: verdict, timestamp, ruleset version used, static check results (per-rule pass/fail with file:line where applicable), dependency chain, DB/template used, install duration, full exit code, path to raw log.
  - `install.log` — raw captured stdout/stderr from the install attempt, unmodified.
  - `README.md` — generated **strictly from `report.json`**, human-readable summary: what failed, where, and the `fix_hint` from the matching ruleset entries. This keeps human and machine views in sync by construction (same pattern as your existing QA validation pipeline architecture).
- Existing report folder for a module is not overwritten silently — either versioned (`_run2`) or archived into history before regeneration, your call, but state the choice explicitly in `DECISIONS.md`.

### 4.8 History & Telemetry Store
- Filesystem layer: `history/valid/<module_name>/<run_id>/` and `history/invalid/<module_name>/<run_id>/` — human-browsable, mirrors report folder contents plus a `telemetry.json` (agent identity/caller ID, run number for this module, previous verdict, duration, ruleset version).
- **Also** maintain a `history/index.sqlite` — one row per run, columns: module_name, verdict, timestamp, duration_ms, agent_id, ruleset_version, report_path. This is what makes "which modules fail most often" or "agent X's first-pass success rate" an actual query instead of manual folder archaeology. Use stdlib `sqlite3` — no ORM needed for a single append-mostly table like this.
- Caller identity (`agent_id`) is passed in via CLI flag or environment variable (`OUV_AGENT_ID`) — e.g. `antigravity`, `manual`, `ci`. Defaults to `unknown` if unset, but the CLI should warn (not fail) when it's unset, so telemetry quality degrades visibly rather than silently.
- **The SQLite schema must store enough data to power the learning loop (Section 4.9 and 5b) and the full lineage system (Section 4.12).**
  Full columns required:
  ```
  fired_rule_ids        TEXT   -- JSON array of rule IDs that fired (static)
  install_error_sig     TEXT   -- normalized error fingerprint from install.log (nullable)
  prev_run_id           TEXT   -- run_id of the immediately preceding run for this module
  fix_agent_id          TEXT   -- agent_id of the fixer (nullable, set by revalidate flow)
  commit_sha            TEXT   -- git commit SHA the module was validated against (nullable if not git-managed)
  fix_diff              TEXT   -- unified diff of all files changed by the fixer for this run (nullable)
  duration_ms           INTEGER -- wall-clock time for the full validation pipeline
  ```
  The `prev_run_id` column creates a linked-list chain over all runs for a module.
  The complete attempt history — verdict, commit SHA, fix_agent_id, duration — is
  reconstructable from this table alone via a single recursive CTE or chained lookup.
  See Section 4.12 for the canonical query pattern.
- **`fix_diff` is stored regardless of whether any ruleset rule fired.** An install-only
  failure with no static rule involved still needs to answer "what did the agent change?".
  Capture this as a unified diff (`git diff HEAD~1 -- <module_path>`) at the point the
  fixer agent's commit is validated. Store as plain text in the SQLite cell (gzip if
  needed, but prefer plain for queryability — diffs for typical module fixes are small).
- **`dispatch_fix_attempts`** — separate table tracking consecutive failed/errored verdicts
  per module for the circuit breaker (Section 4.10). Schema:
  ```sql
  CREATE TABLE fix_attempts (
      module_name          TEXT PRIMARY KEY,
      consecutive_failures INTEGER NOT NULL DEFAULT 0,
      last_attempt_run_id  TEXT,
      status               TEXT NOT NULL DEFAULT 'active'  -- 'active' | 'needs_human_triage'
  );
  ```

### 4.9 Ruleset Learning Engine

> **Addendum v1.2 constraint (supersedes original §4.9 language on auto-promotion):**
> `confirmed_in_module_count`, `confirmed_fixes`, and `install_error_signatures` are
> **passive telemetry only**. They inform a human review queue; they **never** trigger
> a write to `ruleset/*.yaml` on their own. `OUV_AUTO_PROMOTE_THRESHOLD` is repurposed
> as a **sort/priority hint** for the human review queue — it controls which items float
> to the top, not whether anything is auto-applied. Agent-driven confirmations are treated
> with equal or greater scrutiny than human-driven ones (same fix pattern across N modules
> is correlated, not independent). **No code path may write to `ruleset/*.yaml` without a
> human invoking an explicit review command** — no exceptions for confirmation count.

This component closes the feedback loop between the validator, the fixer agent, and the
ruleset. It is invoked **only** when a revalidation run changes verdict from `failed`
to `passed` — that transition is the ground-truth signal that a fix actually worked.

#### Trigger condition
```
prev_verdict == 'failed'  AND  current_verdict == 'passed'
```
The orchestrator detects this when archiving to history (Section 4.8) and calls
`RulesetLearningEngine.on_fix_confirmed(prev_run_id, current_run_id, module_path)`.

#### What the engine does

1. **Load the before/after state.**
   - `prev_report.json` → list of `fired_rule_ids`, `install_error_sig`, and `commit_sha`.
   - `git diff <prev_commit_sha> <current_commit_sha> -- <module_path>` → the exact set
     of lines changed by the fixer. This is authoritative; the repo is always git-managed
     when fixer agents are active (see Section 4.11 prerequisite check).

2. **Classify the fix type** (deterministic, not LLM):
   - `static_rule_fix` — all `fired_rule_ids` from the previous run are now
     absent from the current static check result.
   - `dynamic_only_fix` — previous run had no blocking static rules but failed
     install (the error lived only in `install_error_sig`). Signal to potentially
     promote an ACKNOWLEDGED-UNVERIFIED item.
   - `mixed_fix` — both static rules cleaned up AND install error changed.

3. **For each rule in `fired_rule_ids` that is now resolved:**
   - Extract the concrete before/after pattern from the diff.
   - **Append** to the rule's `confirmed_fixes` list and increment
     `confirmed_in_module_count` — these are passive telemetry fields.
   - If the rule's `fix_hint` is still generic and the diff reveals a more
     precise pattern, **propose** an updated `fix_hint` — write it to
     `ruleset/pending_updates.yaml` and surface it in the review queue.
     The hint is derived mechanically from the diff — **no LLM call**.
   - **Never silently update a rule's pattern, severity, or fix_hint.**
     All writes to `ruleset/*.yaml` require a human invoking `ouv ruleset review`.

4. **For `dynamic_only_fix` — ACKNOWLEDGED-UNVERIFIED promotion check.**
   - Extract `install_error_sig` from the previous run.
   - Match against known ACKNOWLEDGED-UNVERIFIED items.
   - If matched, **enqueue** a promotion proposal to `ruleset/pending_updates.yaml`
     for human review. Include confirmation count and which modules triggered it.
   - `OUV_AUTO_PROMOTE_THRESHOLD` (default: 3) controls how these proposals are
     **sorted** in the review queue — higher count = higher priority. It does **not**
     trigger automatic application of any write.
   - A human runs `ouv ruleset review` to see the queue, approve/reject each proposal,
     and apply selected updates to `odoo_18_19_rules.yaml`. That command is the only
     write path into the ruleset files.

5. **Write a `learning_event.json`** in `history/valid/<module>/<run_id>/`:
   ```json
   {
     "event_type": "fix_confirmed",
     "module": "my_module",
     "prev_run_id": "abc123",
     "prev_commit_sha": "a1b2c3d",
     "current_run_id": "def456",
     "current_commit_sha": "e4f5g6h",
     "rules_resolved": ["ORM_CHECK_ACCESS_RIGHTS_DEPRECATED"],
     "install_error_resolved": null,
     "proposed_ruleset_updates": ["ruleset/pending_updates.yaml#update-1"],
     "auto_applied": false,
     "timestamp": "2026-08-08T17:00:00+06:00"
   }
   ```
   `auto_applied` is **always** `false` — there is no auto-apply path.

6. **Update `history/index.sqlite`** — add a `learning_events` table:
   ```sql
   CREATE TABLE learning_events (
       id                     INTEGER PRIMARY KEY,
       timestamp              TEXT,
       module_name            TEXT,
       prev_run_id            TEXT,
       curr_run_id            TEXT,
       rules_resolved         TEXT,   -- JSON array
       error_fingerprint      TEXT,   -- nullable
       confirmation_count     INTEGER, -- how many modules have confirmed this pattern
       review_priority        INTEGER, -- computed from confirmation_count for queue sorting
       ruleset_version_before TEXT,
       ruleset_version_after  TEXT    -- nullable, set after human applies update
   );
   ```

#### What the engine does NOT do
- **Never write to `ruleset/*.yaml` directly** — all writes are gated behind
  `ouv ruleset review` (explicit human command). No exceptions.
- Never auto-promote based on confirmation count, regardless of the value of
  `OUV_AUTO_PROMOTE_THRESHOLD`. The threshold only sorts the review queue.
- Never call an LLM to generate the updated fix_hint.
- Never change a rule's `severity` or `check_type` automatically.

**Testability:** inject a fake `prev_report.json` + fake file diff + fake
`pending_updates.yaml` writer. The review-queue enqueue step is an injectable
callback (default: appends to `pending_updates.yaml`; testable with an in-memory
queue). The `ouv ruleset review` command is a separate, interactive-only code
path — it has its own test suite exercising the approve/reject/apply flow.

---

### 4.10 Retry Circuit Breaker  *(new — Addendum v1.2, Point 3)*

Prevents fixer agents from looping indefinitely against a module they cannot fix.

- **Tracking table:** `fix_attempts` in `history/index.sqlite` (schema in Section 4.8).
  One row per module. `consecutive_failures` counts back-to-back `failed` or `errored`
  verdicts produced **by a fixer agent** (i.e., `fix_agent_id IS NOT NULL`). A `passed`
  verdict resets the counter to zero.
- **Breach threshold:** `OUV_MAX_FIX_ATTEMPTS` (env var, default: 3).
- **On breach:** orchestrator sets `fix_attempts.status = 'needs_human_triage'` for
  that module and stops dispatching it to fixer agents. A human must reset the status
  (via `ouv triage clear <module>`) before fixer agents can touch it again.
- **`needs_human_triage` is a dispatch-eligibility status, not a verdict state.**
  The four verdict states (`passed`/`failed`/`errored`/`blocked`) remain exactly as
  defined in Section 4.6. `needs_human_triage` lives only in the `fix_attempts` table
  and is surfaced in the run-level summary — it does not appear in `report.json`.
- **Counter scope:** consecutive failures within a fixer-agent dispatch sequence. Manual
  runs (`fix_agent_id IS NULL`) do not increment or reset this counter.
- **Configuring:** `OUV_MAX_FIX_ATTEMPTS=0` disables the circuit breaker entirely
  (useful for testing, warn in CLI output when this is set).

**Testability:** inject a fake `fix_attempts` table state + a fake orchestrator dispatch
call. Assert that dispatch is refused after N failures and that status flips correctly.

---

### 4.11 Dispatch Lock  *(new — Addendum v1.2, Point 4)*

Prevents two fixer agents from touching the same module concurrently.

- **New file:** `src/ouv/dispatch_lock.py`. Backed by the same `history/index.sqlite` file
  — no new dependency.
- **Schema:** add a `dispatch_locks` table:
  ```sql
  CREATE TABLE dispatch_locks (
      module_name    TEXT PRIMARY KEY,
      status         TEXT NOT NULL DEFAULT 'idle',   -- 'idle' | 'in_progress'
      fix_agent_id   TEXT,
      claim_ts       TEXT,   -- ISO-8601 timestamp of when the lock was acquired
      lock_timeout_s INTEGER DEFAULT 1800            -- stale-lock TTL (default: 30 min)
  );
  ```
- **Acquire:** before dispatching a module to a fixer agent, orchestrator calls
  `DispatchLock.acquire(module_name, fix_agent_id)`. This does an atomic
  `INSERT OR REPLACE` only if the current row has `status = 'idle'` OR
  `claim_ts` is older than `lock_timeout_s`. Returns `True` (lock acquired) or
  `False` (module already in-progress by another agent).
- **Release:** the fixer agent (or the orchestrator on the agent's behalf) calls
  `DispatchLock.release(module_name, fix_agent_id)` after `ouv validate` completes.
  The lock is also released automatically when the orchestrator detects a stale claim
  (elapsed > `lock_timeout_s`) — crashed agents don't block forever.
- **Refused modules:** orchestrator logs a `WARN: module X is locked by agent Y (claimed
  at Z). Queuing or skipping.` The scheduling behavior (queue vs. skip) is configurable
  via `--on-lock-conflict [queue|skip]` (default: `queue`).
- **SQLite concurrency:** all lock operations use `BEGIN IMMEDIATE` transactions to avoid
  TOCTOU races in the SQLite WAL. This is safe for the expected concurrency level
  (few dozen fixer agents, not thousands).

**Testability:** mock the SQLite connection; assert that concurrent acquire calls for the
same module are serialized correctly. Test stale-lock expiry with a synthetic `claim_ts`
in the past.

---

### 4.12 Full Lineage Store  *(new — Addendum v1.2, Point 5)*

Makes a module's complete attempt history reconstructable from `history/index.sqlite`
alone, without cross-referencing git manually.

- **Chain:** the `prev_run_id` column in the `runs` table (Section 4.8) forms a
  singly-linked list of all validation attempts for a module, oldest to newest.
  Combined with the `commit_sha` and `fix_diff` columns added in Section 4.8, every
  run records: verdict, commit SHA, fix_agent_id, duration, and the exact diff.
- **Canonical recursive query:**
  ```sql
  WITH RECURSIVE lineage(run_id, module_name, verdict, commit_sha,
                         fix_agent_id, duration_ms, timestamp, prev_run_id) AS (
    -- anchor: the most recent run
    SELECT run_id, module_name, verdict, commit_sha,
           fix_agent_id, duration_ms, timestamp, prev_run_id
    FROM   runs
    WHERE  module_name = :module AND prev_run_id IS NULL
    UNION ALL
    -- recursive: walk the chain forward
    SELECT r.run_id, r.module_name, r.verdict, r.commit_sha,
           r.fix_agent_id, r.duration_ms, r.timestamp, r.prev_run_id
    FROM   runs r
    JOIN   lineage l ON r.prev_run_id = l.run_id
  )
  SELECT * FROM lineage ORDER BY timestamp;
  ```
  Exposed as `ouv history lineage <module_name>` in the CLI.
- **`fix_diff` field:** stored per run (nullable). Populated by the orchestrator
  immediately after the fixer's commit is validated:
  ```python
  fix_diff = subprocess.check_output(
      ["git", "diff", prev_commit_sha, current_commit_sha, "--", module_path],
      text=True
  )
  ```
  Stored even when no static rule fired — "what did the agent change" must be
  answerable for install-only failures too.
- **`commit_sha` in `report.json`:** `report.json` includes a top-level `commit_sha`
  field (the SHA the module was validated against). Rollback is `git revert <sha>` —
  not reconstructed from JSON. If the repo is not git-managed, `commit_sha` is `null`
  and the field is present but explicitly `null` (not omitted).
- **Git prerequisite for fixer-agent mode:** if `fix_agent_id` is non-null and the
  module path is not inside a git repo, the orchestrator raises a hard error:
  `"Fixer-agent mode requires a git-managed repo. Repo not found at <path>."`
  This is a blocker to resolve before enabling fixer-agent dispatch, not a
  condition to work around silently.

**Testability:** construct a small fixture git repo with a known commit history;
assert that the recursive query returns runs in correct order with correct SHAs.

---

## 5. Execution Sequence (single module, conceptual)

### 5a. Normal validation run

1. Discover module → parse manifest
2. Resolve dependencies against graph → if unresolved/cyclic/unlicensed → `blocked`, write report, done
3. Run static rule engine against ruleset → if any `blocking` rule fails → `failed`, write report, done
   - Report includes: rule IDs that fired, file:line location, `fix_hint`, `evidence` tier, `source` URL
4. Acquire scratch DB (clone from appropriate template)
5. Run `odoo-bin -i <module> --stop-after-init --no-http` as subprocess
6. Parse exit code + log → determine `passed` / `failed` / `errored`
   - On failure: fingerprint the install error (`install_error_sig`) for the learning engine
7. (Optional) run module's own test tags if present
8. Aggregate verdict → write `report.json` (including `commit_sha` field), `install.log`, `README.md`
9. Archive into `history/{valid,invalid}/` + append `history/index.sqlite` row
   - Store `fired_rule_ids`, `install_error_sig`, `prev_run_id` (null on first run),
     `commit_sha` (from `git rev-parse HEAD` at the module path — null if not git-managed),
     `fix_diff` (unified diff since prev commit, null on first run or non-git repo),
     `fix_agent_id` (from `OUV_AGENT_ID` if this is a fixer-agent-initiated run)
   - If `fix_agent_id` is non-null: update `fix_attempts` table (circuit breaker, Section 4.10)
   - If `fix_agent_id` is non-null and verdict is `passed`: release dispatch lock (Section 4.11)
10. Purge scratch DB (unless `--keep-db-on-failure` and verdict was failed/errored)

At the batch level, this loop runs across all modules in topological order, parallelized
within concurrency limits, with a run-level summary printed at the end.

### 5b. Fix-then-revalidate loop (learning path)

This flow is invoked when the CLI is called with `ouv revalidate <module_path>` after
the fixer agent has applied changes.  The `--prev-run-id` flag is optional but enables
the learning engine.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. User/fixer agent calls: ouv validate <path> [--agent-id fixer-agent] │
│    (same command as normal — revalidation IS a normal run)              │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                ┌───────────────▼───────────────────┐
                │ Orchestrator runs full pipeline     │
                │ (steps 1-9 of 5a above)            │
                │ current_verdict = ?                │
                └───────────────┬───────────────────┘
                                │
           ┌────────────────────┴────────────────────┐
           │  prev_verdict == 'failed'?               │
           │  current_verdict == 'passed'?            │
           └────────┬──────────────────┬─────────────┘
                    │ YES              │ NO
                    ▼                  ▼
     ┌──────────────────────┐   ┌───────────────────────────┐
     │ RulesetLearningEngine │   │ Normal archive + report   │
     │ .on_fix_confirmed()   │   │ No learning event written │
     │ (Section 4.9)         │   └───────────────────────────┘
     └──────────┬───────────┘
                │
    ┌───────────▼────────────────────────────────────────────────┐
    │ Engine asks user:                                           │
    │  "Rule X was fixed in module Y. Observed fix:              │
    │   BEFORE: <old_code>                                        │
    │   AFTER:  <new_code>                                        │
    │  Update fix_hint in ruleset? [y/N]"                        │
    │                                                             │
    │  For ACKNOWLEDGED-UNVERIFIED promotion:                     │
    │  "Install error '...' was resolved. Matches unverified      │
    │   item #2. Add as COMMUNITY rule? [y/N]"                   │
    └───────────┬────────────────────────────────────────────────┘
                │
         ┌──────▼─────────────────────────────┐
         │ On 'y': write to                   │
         │  ruleset/pending_updates.yaml      │
         │  → apply to odoo_18_19_rules.yaml  │
         │  → bump ruleset version            │
         │  → write learning_event.json       │
         │  → update SQLite learning_events   │
         └────────────────────────────────────┘
```

**Key invariant:** the fixer agent does not need to know about the learning loop.
It just makes code changes and calls `ouv validate`.  The validator is the one
that detects the prev→current verdict transition and triggers learning.

---

## 6. Technology Choices & Trade-offs

| Concern | Choice | Why | Trade-off accepted |
|---|---|---|---|
| CLI framework | `typer` (or `click` — pick one, `typer` is thinner on top of `click`) | Declarative, good `--help` generation, minimal boilerplate | One more dependency vs. `argparse`; justified by long-term ergonomics as commands grow |
| Dependency graph | `networkx` | Battle-tested DAG/cycle-detection/topological-sort, don't hand-roll graph algorithms | Slightly heavier import than a hand-rolled Kahn's algorithm; irrelevant at this scale (127 nodes) |
| XML/view parsing | `lxml` | Fast, real XPath support, handles malformed XML gracefully with clear errors | C-extension dependency; already near-universally available, low risk |
| Python static analysis | stdlib `ast` | Zero extra dependency, sufficient for call/import detection at the depth this needs | Won't catch dynamic patterns (`getattr`-based calls) — acceptable, document as a known limitation |
| YAML parsing | `PyYAML` (or `ruamel.yaml` if you want round-trip-safe editing of the ruleset file) | Standard, boring, correct | `ruamel` is slightly heavier but preserves comments/formatting if humans hand-edit the ruleset — recommend `ruamel.yaml` specifically for the ruleset file since it's human-reviewed |
| Schema validation | `pydantic` | Validates `report.json` schema, ruleset schema, config schema with clear errors instead of silent `KeyError`s downstream | Adds a dependency, but you already use structured JSON contracts elsewhere (per your existing architecture pattern) — consistent choice |
| DB access | `psycopg2` (or `psycopg3`) directly, **not** a full ORM/SQLAlchemy | You're doing `CREATE DATABASE ... TEMPLATE`, `DROP DATABASE`, and connection management — not querying business data. An ORM is pure overhead here | Slightly more manual connection handling; acceptable given the narrow, admin-style usage |
| Telemetry store | stdlib `sqlite3` | Single-writer-mostly, embedded, zero ops overhead, trivially queryable with any SQLite browser | Not for high-concurrency writes — fine, since writes are one-per-module-run, not high frequency |
| Subprocess parallelism | stdlib `concurrent.futures.ProcessPoolExecutor` | Standard, well-understood, no extra dependency | `asyncio` would also work; `concurrent.futures` is more readable for a team that isn't already async-heavy — explicit choice for maintainability over marginal performance |
| Terminal output | `rich` | Good progress bars / tables for run summaries, minimal effort | Cosmetic dependency — acceptable, low risk, no logic ever depends on it |

**General principle applied throughout:** every dependency above solves a problem that's genuinely hard to get right from scratch (DAGs, XML XPath, subprocess pooling, DB access) or meaningfully improves reviewability (pydantic schemas, rich output). Nothing here is decorative-only except `rich`, and even that's isolated to the CLI presentation layer — swapping it out later touches one file, not the core library.
2. Resolve dependencies → ...
3. Run static rule engine → ...
4. Acquire scratch DB
5. Run `odoo-bin` subprocess
6. Parse exit code → determine verdict
7. Run tests
8. Aggregate verdict → write `report.json` (including `commit_sha`)
9. Archive → append `history/index.sqlite` (include `commit_sha`, `fix_diff`, `fix_agent_id`)
   - If `fix_agent_id` is set: check/update `fix_attempts` (4.10) and release dispatch lock (4.11)
10. Purge scratch DB

---

## 7. Trade-offs Explicitly Weighed (for your awareness, not necessarily to resolve now)

1. **Layered DB templates (4.4) vs. flat golden-base-only.** Recommendation: ship flat golden-base first, measure actual per-module install time, then decide if layering is worth it.

2. **Process-pool parallelism vs. sequential.** Parallel is faster; support `--max-parallel 1` for debugging.

3. **Rule severity granularity.** Stick to `blocking`/`warning`/`info`.

4. **Report folder versioning on re-run.** Append-versioned (`_run{n}`) with `latest` symlink.

5. **Enterprise licensing.** Fail loudly (`blocked`) if EE modules validated against CE-only base.

---

## 8. Extensibility Design

- **New check types:** the `callable` check type (4.3) is the escape hatch — anything not expressible as regex/ast/xpath gets a small Python function, still driven by the same YAML-declared rule metadata (id, severity, applies_to).
- **New report consumers:** because `report.json` is the canonical artifact and schema-validated (pydantic), any future tool (a dashboard, a Slack notifier, a different agent) can consume it without touching the validator internals.
- **Service/daemon mode (Phase 2, not built now):** because the core logic lives in a library (not tangled into the CLI), wrapping it in a queue-worker or long-running service later is an additive change — a new entrypoint calling the same `Orchestrator` class, not a rewrite.
- **Video evidence (Phase 3, not built now):** the report folder structure already has room for this — a future `evidence/` subdirectory per report, populated by a separate UI-walkthrough tool that reads the same `report.json` to know which module/scenario to walk through.

---

## 9. Deliverables Checklist (Phase 1 — build now)

- [ ] `src/ouv/` package (rename `ouv` to your preferred internal tool name)
- [ ] `discovery.py` — Module Discoverer (4.1)
- [ ] `graph.py` — Dependency Graph Builder (4.2)
- [ ] `static_checks.py` — Static Analysis Engine (4.3)
- [ ] `db_templates.py` — DB Template Manager (4.4)
- [ ] `installer.py` — Install Executor Pool (4.5)
- [ ] `verdict.py` — Verdict Aggregator (4.6)
- [ ] `reporting.py` — Report Generator (4.7)
- [ ] `history.py` — History & Telemetry Store (4.8)
- [ ] `orchestrator.py` — ties everything together, the one class Phase 2 will reuse
- [ ] `cli.py` — `typer`-based CLI, exposes `ouv validate <path...>` with flags: `--max-parallel`, `--keep-db-on-failure`, `--agent-id`, `--ruleset-path` (defaults to bundled), `--dry-run` (static checks only, skip DB phase)
- [x] `ruleset/odoo_18_19_rules.yaml` — built per Section 3.1. Research pass complete (27 rules, tiered evidence). OFFICIAL rules: 11, COMMUNITY rules: 16. Acknowledged-unverified: 8 items documented in YAML but excluded from rules.
- [ ] `tests/` — unit tests per component (per testability notes in Section 4), plus an `integration/` tier requiring real Postgres + Odoo 19 binary
- [ ] `SYSTEM_MAP.md`, `DECISIONS.md` — living docs, started day one
- [ ] `README.md` at repo root — setup instructions (Postgres, Odoo 19 addons-path config, how to build the golden template DB)
- [ ] `pyproject.toml` — installable package, pinned dependency versions

---

## 10. Explicit Non-Implementation Notes for This Phase

- Do not build Phase 2 (service/daemon) or Phase 3 (video evidence) now. Section 8 exists so the Phase 1 code doesn't have to be rewritten to support them later — that's the only reason they're mentioned here at all.
- Do not attempt to install or manage multiple full Odoo *server* instances. The only "instance" concept in this system is the single persistent Odoo 19 install already on the machine (source of `odoo-bin` and addons-path) plus ephemeral `--stop-after-init --no-http` subprocesses against scratch databases.

---

## 11. Verdict on This Spec (updated after ruleset research pass)

**What changed in v1.1:** Section 3.1 no longer blocks on a human review gate. The
ruleset was built using a tiered evidence model (OFFICIAL / COMMUNITY / ACKNOWLEDGED-UNVERIFIED)
that makes the confidence level machine-readable and surfaced in every report.  COMMUNITY
evidence required 3+ independent, solved/accepted 2025 sources — this is a reproducible,
audit-able bar rather than an opaque "trust me" claim.  The 8 ACKNOWLEDGED-UNVERIFIED items
are clearly excluded from rules and listed for future confirmation.

**Ruleset completeness assessment:**
- 11 OFFICIAL rules (primary-source URLs in the YAML) — highest confidence, use immediately.
- 16 COMMUNITY rules (3+ solved-issue sources) — high confidence, the engine surfaces the
  evidence tier in every report so developers can verify against their own Odoo 19 install.
- 8 ACKNOWLEDGED-UNVERIFIED items excluded from rules — the biggest outstanding gap is the
  exact list of merged/removed modules (community says 25–47, no enumeration available).
  This is where false negatives are most likely — a module depending on a renamed/removed
  Odoo module will reach the DB phase and fail there rather than being caught statically.
  The install executor will catch it; it just won't have a nice static rule to point to.

**Where risk still sits:**
- COMMUNITY rules are accurate to the best of current research, but if a rule is wrong it
  produces a false `failed` (wasted developer time, not a live breakage).  The evidence tier
  field in the report and the `source` URLs mean every flagged file/line can be manually
  verified in under 2 minutes — this is the correct tradeoff.
- False `passed` risk (a module the validator says is OK but actually breaks): highest for
  the ACKNOWLEDGED-UNVERIFIED items (especially renamed models/fields) and for runtime-only
  patterns the static AST checker can't see (dynamic `getattr` calls, string-based model
  access).  The DB install phase exists precisely to catch these; the static phase is a
  first-pass filter, not a guarantee.

**What I'd watch during Phase 1 build:**
- The `_sql_constraints` rule uses `ast_pattern: attribute_assignment_name` — this is a
  non-standard AST pattern string.  When implementing the AST checker, make sure the
  callable/AST dispatch handles assignment targets (not just call expressions).
- The layered-DB-template optimization (4.4, trade-off #1) — still recommend shipping
  flat-template-only first and measuring before building the layered version.

**Net assessment:** architecture is sound, ruleset is research-complete.  Implementation
can start.  The one active task before production use is QA validation of the 16
COMMUNITY rules against a real Odoo 19 install during integration testing — this happens
naturally as part of building and running the integration test suite.

---

## 12. Addendum v1.2 — Autonomy Boundaries for Fixer-Agent Handoff

**Addendum date:** 2026-08-08  
**Supersedes:** conflicting language in Section 4.9 (auto-promotion write path)  
**Sequencing note:** Points 2–5 are built in the first implementation pass (lock/lineage
retrofitting after fixer agents are running is worse than building it now). Point 1 is a
constraint on existing §4.9 code — audit and confirm no write path bypasses it before
this addendum is marked done.

### A1. Ruleset mutation — fully gated, no exceptions

No code path may write to `ruleset/*.yaml` without a human invoking `ouv ruleset review`.
`confirmed_in_module_count`, `confirmed_fixes`, and `install_error_signatures` remain
passive telemetry only — they inform the review queue, they never trigger a write.
`OUV_AUTO_PROMOTE_THRESHOLD` is a sort/priority hint for that queue, not an auto-fire
trigger. Agent-driven confirmations receive equal or greater scrutiny than human-driven
ones (same fix pattern across N modules is correlated, not independent evidence).

See Section 4.9 for the full updated engine specification.

### A2. Every fix attempt is a git commit

The fixer agent commits its change before triggering re-validation.
`report.json` stores the `commit_sha` it validated against.
Rollback is `git revert <sha>` — not reconstructed from JSON.
If the repo is not git-managed when a fixer agent touches it, the orchestrator raises
a hard error (`"Fixer-agent mode requires a git-managed repo"`). This is a blocker to
resolve before enabling fixer-agent dispatch, not a gap to work around.

See Section 4.12 for git integration details.

### A3. Retry circuit breaker

Default limit: 3 consecutive `failed`/`errored` verdicts from fixer agents for the same
module. On breach: orchestrator stops dispatching that module and marks it
`needs_human_triage` in `fix_attempts` table. Configurable via `OUV_MAX_FIX_ATTEMPTS`.
`needs_human_triage` is a dispatch-eligibility status — it does not appear as a verdict
state in `report.json`.

See Section 4.10 for full specification.

### A4. Concurrency lock for parallel fixer agents

Before a module is dispatched, orchestrator acquires a `dispatch_locks` row keyed by
module name with a `claim_ts` and `fix_agent_id`. A second agent requesting the same
module is refused or queued. Stale locks (default: 30 min) are cleared automatically.
Implemented in `dispatch_lock.py`, backed by `history/index.sqlite`.

See Section 4.11 for full specification.

### A5. Full lineage per module

A module's complete attempt history — verdict, commit SHA, fix_agent_id, duration,
fix_diff — is reconstructable from `history/index.sqlite` alone via a single recursive
CTE. `fix_diff` is stored per run regardless of whether any ruleset rule fired.

See Section 4.12 for the canonical query and schema.

### A6. What autonomy is explicitly allowed

Fixer agents may, without any approval gate:
- Read `report.json` and `install.log`
- Write code changes to the module directory
- Commit those changes via git
- Call `ouv validate` to re-check, repeatedly, unattended

The validator's verdict from re-validation is the ground truth regardless of what the
agent believed it fixed. The **only** gate is on the ruleset file itself (Point A1 above).
