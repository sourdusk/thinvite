# Contributing to Thinvite

Thanks for your interest in contributing! Thinvite is beta software and we welcome bug reports, feature suggestions, and pull requests.

## Reporting Issues

- Search [existing issues](https://github.com/sourk9/thinvite/issues) before opening a new one.
- Include steps to reproduce, expected behavior, and actual behavior.
- For security vulnerabilities, please email the maintainer directly instead of opening a public issue.

## Development Setup

1. Follow the [Setup](README.md#setup) instructions in the README to get the app running locally.

2. Install test dependencies in a virtualenv:
   ```bash
   python -m venv web/.venv
   source web/.venv/bin/activate
   pip install -r web/requirements.txt
   ```

3. Run the test suite:
   ```bash
   pytest web/tests/
   ```

4. The app auto-reloads on file changes (via watchfiles). Restart the container only when changing environment variables:
   ```bash
   docker compose restart python
   ```

## Project Structure

```
thinvite/
├── web/
│   ├── main.py          # Pages, middleware, session handling, EventSub webhook
│   ├── db.py            # Database pool and queries
│   ├── twitch.py        # Twitch OAuth and Helix API helpers
│   ├── discorddb.py     # Discord OAuth and bot invite creation
│   ├── bot.py           # EventSub webhook subscription management
│   ├── expiry.py        # Background scheduler (redemption expiry, token refresh)
│   ├── sanitize.py      # Input validators
│   ├── mail.py          # Mailjet email helpers
│   ├── captcha.py       # Cloudflare Turnstile verification
│   └── tests/           # pytest test suite
├── db/
│   ├── init.sql         # Initial schema (runs on first DB volume init)
│   └── migrate_*.sql    # Incremental migrations
├── docker-compose.yml
└── Dockerfile
```

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR.
- Include tests for new functionality. The test suite is in `web/tests/` and uses pytest with `pytest-asyncio` and `pytest-mock`.
- All queries must use parameterized statements (`%s` placeholders) — never interpolate user input into SQL.
- Use the `db._acquire()` context manager for database access (not `db._pool.acquire()` directly) to get automatic reconnection on connection failures.
- Run the test suite before submitting and confirm all tests pass.

## Database Changes

- Never modify `db/init.sql` alone — existing deployments won't see the change.
- Create a new `db/migrate_NNN.sql` file for schema changes, and also update `db/init.sql` so fresh installs get the full schema.
- Document what the migration does in a SQL comment at the top of the file.

## Style

- Python code follows standard conventions — no specific formatter is enforced, but keep it consistent with the existing codebase.
- Commit messages should be concise and describe the "why", not just the "what".

## License

By contributing, you agree that your contributions will be licensed under the [GNU Affero General Public License v3](LICENSE).
