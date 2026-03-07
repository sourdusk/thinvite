# Thinvite

Thinvite is an open-source solution that securely invites users to your Discord server by integrating Twitch channel point redeems with Discord invites. This limits the exposure of invite links and helps prevent malicious actors from accessing your community.

## Features

- **Twitch channel point integration** — link a custom redeem to automatic Discord invite generation
- **Single-use, time-limited invites** — each invite is unique and expires after 24 hours
- **Automatic redemption management** — duplicates are refunded; fulfilled redeems are marked on Twitch
- **EventSub webhooks** — real-time Twitch notifications without persistent connections
- **Streamer dashboard** — view redemption stats, manage pending invites, bulk revoke
- **Beta gate** — restrict access to an allowlist of Twitch usernames
- **Captcha & rate limiting** — Cloudflare Turnstile and per-IP rate limiting on all API callbacks
- **Contact form & waitlist** — Mailjet-powered email notifications
- **Web-based UI** built with NiceGUI and Quasar

## Requirements

- Docker and Docker Compose
- A [Twitch Developer Application](https://dev.twitch.tv/console)
- A [Discord Application](https://discord.com/developers/applications) with a bot
- A reverse proxy with HTTPS (e.g. Caddy, nginx)
- A [Cloudflare Turnstile](https://dash.cloudflare.com) site (for captcha)
- A [Mailjet](https://www.mailjet.com) account (for contact form and waitlist emails)

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/sourk9/thinvite.git
   cd thinvite
   ```

2. Create a `.env` file in the root directory for Docker Compose:
   ```
   MARIADB_ROOT_PASSWORD=your_root_password
   MARIADB_PASSWORD=your_db_password
   ```

3. Create a `web/.env` file for the application (see `.env.example` for all options):
   ```
   SITE_URL=https://thinvite.example.com
   THINVITE_DB_PASSWORD=your_db_password
   THINVITE_TWITCH_ID=your_twitch_client_id
   THINVITE_TWITCH_SECRET=your_twitch_client_secret
   THINVITE_DISCORD_ID=your_discord_client_id
   THINVITE_DISCORD_SECRET=your_discord_client_secret
   THINVITE_DISCORD_BOT_TOKEN=your_discord_bot_token
   NICEGUI_STORAGE_SECRET=generate_with_secrets_token_hex_32
   THINVITE_EVENTSUB_SECRET=generate_with_secrets_token_hex_32
   MAILJET_API_KEY=your_mailjet_api_key
   MAILJET_SECRET_KEY=your_mailjet_secret_key
   MAILJET_SENDER_EMAIL=noreply@example.com
   TURNSTILE_SITE_KEY=your_turnstile_site_key
   TURNSTILE_SECRET_KEY=your_turnstile_secret_key
   ```

4. Start the services:
   ```bash
   docker compose up -d
   ```

5. Set up your reverse proxy to forward HTTPS traffic to `127.0.0.1:8083`.

6. Access the application at your configured `SITE_URL`.

## Twitch OAuth Redirect URIs

In your Twitch Developer Console, add these redirect URIs:

```
https://your-domain.com/api/twitch/auth_code
https://your-domain.com/api/twitch/viewer_auth
```

## Discord OAuth Redirect URI

In your Discord Developer Portal, add this redirect URI:

```
https://your-domain.com/api/discord
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Web App | 8083 | Main Thinvite application (localhost only) |
| MariaDB | — | Database (internal only) |

## Database Migrations

The initial schema is applied automatically on first run. For existing installations, apply migrations manually:

```bash
docker exec -i thinvite-db mariadb -u thinvite -p"YOUR_DB_PASSWORD" thinvite < db/migrate_001.sql
docker exec -i thinvite-db mariadb -u thinvite -p"YOUR_DB_PASSWORD" thinvite < db/migrate_002.sql
docker exec -i thinvite-db mariadb -u thinvite -p"YOUR_DB_PASSWORD" thinvite < db/migrate_003.sql
```

## Beta Gate

To restrict access during beta, create a `web/beta_users.txt` file with one Twitch username per line. Lines starting with `#` are comments. Remove or empty the file to open access to everyone.

## License

This program is free software: you can redistribute it and/or modify it under the terms of the [GNU Affero General Public License v3](LICENSE).
