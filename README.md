# ade-demo-retail

Pipeline code for `ADE_DEMO_RETAIL`, the retail demo that [ADE Mission Control](https://github.com/sfc-gh-jleong/ade-mission-control) watches. When a job in this pipeline fails, Mission Control investigates it, rehearses a fix on a zero-copy clone, has a second agent review it, and opens a pull request here. A person approves and merges. GitHub Actions deploys.

```
orders (RAW) ──> CLEAN_ORDERS ──> BUILD_DAILY_REVENUE ──> PUBLISH_SNAPSHOT
                      │                    │                      │
              STAGING.ORDERS_CLEAN   MART.DAILY_REVENUE   MART.REVENUE_SNAPSHOTS
customers (RAW) ──> MART.CUSTOMER_LTV (dynamic table)
```

## What's here

| Path | What it is |
|---|---|
| `objects/<schema>/<object>.sql` | One file per object, one statement per file, written with the `{{DB}}` placeholder |
| `deploy/deploy.sql` | Applies every object file in order. Each task is suspended, changed and resumed |
| `deploy/record_deploy.sql` | Writes a row to `DEPLOY.CHANGE_HISTORY` before the pipeline runs |
| `deploy/run_pipeline.sql` | Runs the task graph once and refreshes the dynamic table |
| `scripts/validate.py` | The `validate` check on every pull request |
| `scripts/reset-to-baseline.sh` | Resets the demo after a session |

Not in this repo, on purpose: the `RAW` and `REF` schemas, their data, and the root task `LOAD_ORDERS`. They play the source system that the Chaos Lab scenarios change. If they were deployed from here, every deploy would undo the scenario.

## How a change reaches Snowflake

1. A pull request changes a file in `objects/`. Mission Control's pull requests come from the `ade/` branches and carry the `ade` label, plus informational `ade/rehearsal` and `ade/review` statuses.
2. `validate` checks the SQL. It needs no Snowflake access, so pull requests from forks never receive credentials.
3. A code owner approves, then merges. Branch protection on `main` requires both, and dismisses approvals when new commits are pushed.
4. `Deploy` runs on the push to `main`. It signs in to Snowflake through OIDC as `GHA_ADE_DEMO_SVC`, so no password, key or token is stored here. It applies the objects, records the deploy, then runs the pipeline once.
5. Mission Control sees the deploy row, waits for the next successful run, and resolves the incident.

The rules `validate` enforces:
- Each file holds one statement for the object its path names.
- Tables use `CREATE TABLE IF NOT EXISTS`. The dynamic table and tasks use `CREATE OR ALTER`, so re-applying an unchanged file does nothing.
- No `CREATE OR REPLACE`, `DROP`, `TRUNCATE`, grants, `CALL`, `EXECUTE` or `USE`.
- No literal database names; every reference uses `{{DB}}`.
- Tasks keep their `AFTER` clause.
- The dynamic table uses `REFRESH_MODE = FULL`. Demo resets recreate its source table, and incremental refresh fails once a source table is recreated.

Run it locally with `python3 scripts/validate.py`.

## Resetting the demo

Merged fixes stay on `main`. Most are harmless to later scenarios, but two change what the code expects from the source data: `upstream_column_rename` (reads `SALES_CHANNEL`) and `dt_column_rename` (reads `SALES_REGION`). After a session, run:

```bash
scripts/reset-to-baseline.sh
```

It closes open ADE pull requests and deletes their branches. Next it resets the source data in Snowflake without running the pipeline. Then it restores `objects/` from the `baseline` tag in one commit, pushed with an admin's override of branch protection. The deploy that follows runs the pipeline against matching code and data.

If you change `objects/` on purpose, move the tag afterwards: `git tag -f baseline && git push -f origin baseline`.
