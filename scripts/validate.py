#!/usr/bin/env python3
"""Checks the SQL in objects/ and deploy/deploy.sql. Standard library only;
needs no Snowflake access, so it is safe to run on pull requests from forks.

Each objects/<schema>/<file>.sql must hold exactly one statement that defines
the object its path names, written with the {{DB}} placeholder:
  _schema.sql   CREATE SCHEMA IF NOT EXISTS {{DB}}.<SCHEMA>
  <name>.sql    CREATE TABLE IF NOT EXISTS {{DB}}.<SCHEMA>.<NAME>
                CREATE OR ALTER DYNAMIC TABLE {{DB}}.<SCHEMA>.<NAME>
                CREATE OR ALTER TASK {{DB}}.<SCHEMA>.<NAME>
deploy/deploy.sql must !source every object file exactly once, schemas
first, with each task between ALTER TASK ... SUSPEND and ALTER TASK ... RESUME.

Usage: python3 scripts/validate.py [repo_root]
"""
import pathlib
import re
import sys

PLACEHOLDER = "{{DB}}"
IDENT = r'(?:[A-Za-z_][A-Za-z0-9_$]*|"[^"]+")'

HEADERS = {
    "TABLE": re.compile(r"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+\{\{DB\}\}\.(\w+)\.(\w+)\b", re.I),
    "DYNAMIC TABLE": re.compile(r"^CREATE\s+OR\s+ALTER\s+DYNAMIC\s+TABLE\s+\{\{DB\}\}\.(\w+)\.(\w+)\b", re.I),
    "TASK": re.compile(r"^CREATE\s+OR\s+ALTER\s+TASK\s+\{\{DB\}\}\.(\w+)\.(\w+)\b", re.I),
}
SCHEMA_HEADER = re.compile(r"^CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+\{\{DB\}\}\.(\w+)\s", re.I)

FORBIDDEN = [
    (r"\bCREATE\s+OR\s+REPLACE\b", "CREATE OR REPLACE drops the object (and a table's data); use CREATE OR ALTER or CREATE ... IF NOT EXISTS"),
    (r"\bDROP\b", "DROP is not allowed"),
    (r"\bUNDROP\b", "UNDROP is not allowed"),
    (r"\bTRUNCATE\b", "TRUNCATE is not allowed"),
    (r"\b(GRANT|REVOKE)\b", "grants are managed outside this repo"),
    (r"\bSWAP\s+WITH\b", "SWAP WITH is not allowed"),
    (r"\bCALL\b", "procedure calls are not allowed"),
    (r"\bEXECUTE\s+(IMMEDIATE|TASK|AS)\b", "EXECUTE IMMEDIATE / EXECUTE TASK / EXECUTE AS are not allowed"),
    (r"\bUSE\s+(ROLE|SECONDARY|WAREHOUSE|DATABASE|SCHEMA)\b", "USE statements are not allowed"),
    (r"\bALTER\s+(SESSION|ACCOUNT|USER|WAREHOUSE|DATABASE)\b", "session, account, user, warehouse and database changes are not allowed"),
    (r"\bCOPY\s+INTO\b", "COPY INTO is not allowed"),
    (r"\bSYSTEM\$", "system functions are not allowed"),
    (r"\bADE_(DB|SANDBOX)\b", "references an ADE Mission Control database"),
]


def scan(sql):
    """Splits on semicolons outside strings, quoted identifiers, comments and
    $$ blocks. Returns (statements, code) where code has string literals
    blanked and comments removed, for keyword checks."""
    statements, current, code = [], [], []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            current.append(sql[i:j])
            code.append(" ")
            i = j
        elif two == "/*":
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            current.append(sql[i:j])
            code.append(" ")
            i = j
        elif two == "$$":
            j = sql.find("$$", i + 2)
            j = n if j < 0 else j + 2
            current.append(sql[i:j])
            code.append("''")
            i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if sql[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            current.append(sql[i:j + 1])
            code.append("''")
            i = j + 1
        elif c == '"':
            j = sql.find('"', i + 1)
            j = n - 1 if j < 0 else j
            current.append(sql[i:j + 1])
            code.append(sql[i:j + 1])
            i = j + 1
        elif c == ";":
            statements.append("".join(current))
            current = []
            code.append(";")
            i += 1
        else:
            current.append(c)
            code.append(c)
            i += 1
    if "".join(current).strip():
        statements.append("".join(current))
    return [s.strip() for s in statements if s.strip()], "".join(code)


def check_object_file(path, rel, errors):
    text = path.read_text(encoding="utf-8")
    where = str(rel)

    def err(msg):
        errors.append(f"{where}: {msg}")

    if any(line.lstrip().startswith("!") for line in text.splitlines()):
        err("client commands (lines starting with !) are not allowed in object files")
    tokens = set(re.findall(r"\{\{.*?\}\}|\{%|\{#", text))
    if tokens - {PLACEHOLDER}:
        err(f"only the {PLACEHOLDER} placeholder is allowed (found {', '.join(sorted(tokens - {PLACEHOLDER}))})")
    if "<%" in text:
        err(f"use {PLACEHOLDER}, not <% %> variables")

    statements, code = scan(text)
    flat = re.sub(r"\s+", " ", code).strip()
    for pattern, reason in FORBIDDEN:
        if re.search(pattern, flat, re.I):
            err(reason)

    # Every database-qualified name must use {{DB}}.
    masked = re.sub(r"\{\{DB\}\}\s*\.\s*" + IDENT + r"\s*\.\s*" + IDENT, " ADE_OBJREF ", flat)
    masked = re.sub(r"\{\{DB\}\}\s*\.\s*" + IDENT, " ADE_SCHEMAREF ", masked)
    if PLACEHOLDER in masked:
        err(f"{PLACEHOLDER} must be followed by .<schema> or .<schema>.<object>")
    literal = re.findall(r"\b" + IDENT + r"\s*\.\s*" + IDENT + r"\s*\.\s*" + IDENT, masked)
    if literal:
        err(f"use {PLACEHOLDER} instead of a literal database name ({', '.join(sorted(set(literal)))})")

    schema_dir = rel.parts[1].upper()
    stem = rel.stem
    # A file that fails the checks below is still classified by what it tries
    # to create, so deploy.sql is checked for it too.
    attempted = "TASK" if re.match(r"^CREATE\s+(OR\s+(ALTER|REPLACE)\s+)?TASK\b", flat, re.I) else "UNKNOWN"
    unknown = (attempted, schema_dir, None if stem == "_schema" else stem.upper())
    if len(statements) != 1:
        err(f"must hold exactly one statement (found {len(statements)})")
        return unknown
    if not code.rstrip().endswith(";"):
        err("the statement must end with a semicolon")
    stmt_code = flat.rstrip(";").strip()

    if stem == "_schema":
        m = SCHEMA_HEADER.match(stmt_code + " ")
        if not m or m.group(1).upper() != schema_dir:
            err(f"must be CREATE SCHEMA IF NOT EXISTS {PLACEHOLDER}.{schema_dir}")
        return ("SCHEMA", schema_dir, None)

    for kind, header in HEADERS.items():
        m = header.match(stmt_code)
        if m:
            break
    else:
        err("must be CREATE TABLE IF NOT EXISTS, CREATE OR ALTER DYNAMIC TABLE or CREATE OR ALTER TASK "
            f"on {PLACEHOLDER}.<schema>.<object>")
        return unknown
    schema, name = m.group(1).upper(), m.group(2).upper()
    if (schema, name) != (schema_dir, stem.upper()):
        err(f"defines {schema}.{name}, but its path names {schema_dir}.{stem.upper()}")

    header_part = re.split(r"\bAS\b", stmt_code, maxsplit=1, flags=re.I)[0]
    if kind == "TASK":
        if not re.search(r"\bAFTER\s+\{\{DB\}\}\.\w+\.\w+", header_part, re.I):
            err("a task in this repo must keep its AFTER clause (its place in the task graph)")
        if re.search(r"\bSCHEDULE\s*=", header_part, re.I):
            err("only the root task has a schedule, and the root task is not deployed from this repo")
    if kind == "DYNAMIC TABLE":
        if not re.search(r"\bREFRESH_MODE\s*=\s*FULL\b", header_part, re.I):
            err("dynamic tables here must set REFRESH_MODE = FULL: the source tables are recreated by "
                "demo resets, and incremental refresh fails once a source table is recreated")
    return (kind, schema, name)


def check_deploy_script(root, objects, errors):
    path = root / "deploy" / "deploy.sql"
    if not path.exists():
        errors.append("deploy/deploy.sql is missing")
        return
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    sourced = []
    seen_schemas = set()
    for idx, line in enumerate(lines):
        m = re.match(r"^!source\s+(\S+)$", line)
        if not m:
            if line.startswith("!"):
                errors.append(f"deploy/deploy.sql:{idx + 1}: only !source commands are allowed")
            continue
        rel = pathlib.PurePosixPath(m.group(1))
        sourced.append(str(rel))
        info = objects.get(str(rel))
        if info is None:
            errors.append(f"deploy/deploy.sql:{idx + 1}: {rel} is not an object file in objects/")
            continue
        kind, schema, name = info
        if kind == "SCHEMA":
            seen_schemas.add(schema)
            continue
        if schema not in seen_schemas:
            errors.append(f"deploy/deploy.sql:{idx + 1}: {rel} runs before objects/{schema.lower()}/_schema.sql")
        if kind == "TASK":
            fqn = rf"\{{\{{DB\}}\}}\.{schema}\.{name}"
            before = lines[idx - 1] if idx > 0 else ""
            after = lines[idx + 1] if idx + 1 < len(lines) else ""
            if not re.match(rf"^ALTER\s+TASK\s+IF\s+EXISTS\s+{fqn}\s+SUSPEND;$", before, re.I):
                errors.append(f"deploy/deploy.sql:{idx + 1}: {rel} must follow ALTER TASK IF EXISTS {PLACEHOLDER}.{schema}.{name} SUSPEND;")
            if not re.match(rf"^ALTER\s+TASK\s+{fqn}\s+RESUME;$", after, re.I):
                errors.append(f"deploy/deploy.sql:{idx + 1}: {rel} must be followed by ALTER TASK {PLACEHOLDER}.{schema}.{name} RESUME;")
    for rel in sorted(objects):
        count = sourced.count(rel)
        if count != 1:
            errors.append(f"deploy/deploy.sql must !source {rel} exactly once (found {count})")


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    errors, objects = [], {}
    files = sorted((root / "objects").glob("**/*.sql"))
    if not files:
        errors.append("objects/ holds no .sql files")
    for path in files:
        rel = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        if len(rel.parts) != 3:
            errors.append(f"{rel}: object files live at objects/<schema>/<object>.sql")
            continue
        info = check_object_file(path, rel, errors)
        if info:
            objects[str(rel)] = info
    check_deploy_script(root, objects, errors)
    if errors:
        print(f"validate: {len(errors)} problem(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"validate: {len(objects)} object files and deploy/deploy.sql look good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
