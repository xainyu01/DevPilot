#!/bin/sh
set -eu

# psycopg/libpq accepts a protected password file, keeping the database URL and
# process arguments free of the database password.
if [ -n "${CODEASSIST_POSTGRES_PASSWORD_FILE:-}" ]; then
    umask 077
    PGPASSFILE="/tmp/codeassist.pgpass"
    printf 'database:5432:codeassist:codeassist:%s\n' "$(cat "$CODEASSIST_POSTGRES_PASSWORD_FILE")" > "$PGPASSFILE"
    export PGPASSFILE
fi

# Migrations run before the service accepts traffic. The command is idempotent
# and exits non-zero on a schema failure, preventing a partially upgraded app.
uv run alembic upgrade head
exec "$@"
