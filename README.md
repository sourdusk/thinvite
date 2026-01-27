# Thinvite

Thinvite is an open-source solution that securely invites users to your Discord server by integrating Twitch channel point redeems with Discord invites. This limits the exposure of invite links and helps prevent malicious actors from accessing your community.

## Features

- Twitch authentication and channel point redeem integration
- Discord bot integration for secure invite management
- Web-based UI built with NiceGUI
- Docker containerized deployment

## Requirements

- Docker and Docker Compose
- Twitch Developer Application credentials
- Discord Bot credentials

## Setup

1. Clone the repository

2. Create a `.env` file in the root directory with:
   ```
   MARIADB_ROOT_PASSWORD=your_root_password
   MARIADB_PASSWORD=your_db_password
   ```

3. Create a `web/.env` file with:
   ```
   NICEGUI_STORAGE_SECRET=your_storage_secret
   THINVITE_DB_PASSWORD=your_db_password
   THINVITE_TWITCH_ID=your_twitch_client_id
   THINVITE_TWITCH_SECRET=your_twitch_client_secret
   THINVITE_DISCORD_ID=your_discord_client_id
   THINVITE_DISCORD_SECRET=your_discord_client_secret
   ```

4. Start the services:
   ```bash
   docker compose up -d
   ```

5. Access the application at `http://localhost:8083`

## Services

| Service | Port | Description |
|---------|------|-------------|
| Web App | 8083 | Main Thinvite application |
| DB Admin | 8084 | Database administration (AdminerEvo) |
| MariaDB | - | Database (internal only) |

## License

Open source - see LICENSE file for details.
