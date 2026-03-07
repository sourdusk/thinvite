# Thinvite — Claude Code Guide

## Container commands
- Restart app: `docker compose restart python` (service name is `python`)
- View logs: `docker logs thinvite-web --tail=30`
- App auto-reloads on file edits (watchfiles, volume-mounted); restart only needed for env changes
## Database
- Run a migration: `docker exec -i thinvite-db mariadb -u thinvite -p"$(grep MARIADB_PASSWORD .env | cut -d= -f2)" thinvite < db/migrate_NNN.sql`
- Verify schema: `docker exec thinvite-db mariadb -u thinvite -p"$(grep MARIADB_PASSWORD .env | cut -d= -f2)" thinvite -e "DESCRIBE table_name;"`
- `docker-entrypoint-initdb.d` only runs on first volume init — all schema changes need explicit migrations
- `get_redemptions_for_streamer` uses `SELECT *`, so new columns are returned automatically; use `.get()` defensively in Python row readers

## NiceGUI / Quasar gotchas (see also MEMORY.md)
- Scoped table slots: `table.add_slot("body-cell-<col_name>", "<q-td ...>...</q-td>")`
- Vue `:class` object: OR two statuses with `||` e.g. `props.row.status === 'A' || props.row.status === 'B'`

## Files to never commit
- `web/.env.old` — contains old secrets, not yet gitignored; exclude explicitly when staging
