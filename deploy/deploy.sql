-- Applies every object in objects/ to {{DB}}. Safe to re-run: tables use
-- CREATE TABLE IF NOT EXISTS, the dynamic table and tasks use CREATE OR ALTER,
-- which leaves an unchanged object as it is.
--
-- Order: schemas, tables, dynamic table, then tasks in graph order. A task in
-- a graph must be suspended while it changes, so each one is suspended,
-- altered and resumed. The root task (LOAD_ORDERS) and the RAW / REF source
-- tables belong to the source system and are not deployed from this repo.
--
-- Run from the repository root:
--   snow sql --enable-templating JINJA -D "DB=ADE_DEMO_RETAIL" -f deploy/deploy.sql

!source objects/staging/_schema.sql
!source objects/mart/_schema.sql
!source objects/orchestration/_schema.sql
!source objects/deploy/_schema.sql

!source objects/staging/orders_clean.sql
!source objects/mart/daily_revenue.sql
!source objects/mart/revenue_snapshots.sql
!source objects/deploy/change_history.sql

!source objects/mart/customer_ltv.sql

ALTER TASK IF EXISTS {{DB}}.ORCHESTRATION.CLEAN_ORDERS SUSPEND;
!source objects/orchestration/clean_orders.sql
ALTER TASK {{DB}}.ORCHESTRATION.CLEAN_ORDERS RESUME;

ALTER TASK IF EXISTS {{DB}}.ORCHESTRATION.BUILD_DAILY_REVENUE SUSPEND;
!source objects/orchestration/build_daily_revenue.sql
ALTER TASK {{DB}}.ORCHESTRATION.BUILD_DAILY_REVENUE RESUME;

ALTER TASK IF EXISTS {{DB}}.ORCHESTRATION.PUBLISH_SNAPSHOT SUSPEND;
!source objects/orchestration/publish_snapshot.sql
ALTER TASK {{DB}}.ORCHESTRATION.PUBLISH_SNAPSHOT RESUME;
