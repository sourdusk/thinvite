-- Thinvite — link Twitch channel-point redemptions to single-use Discord invites.
-- Copyright (C) 2026  sourk9
--
-- This program is free software: you can redistribute it and/or modify
-- it under the terms of the GNU Affero General Public License as published by
-- the Free Software Foundation, either version 3 of the License, or
-- (at your option) any later version.
--
-- This program is distributed in the hope that it will be useful,
-- but WITHOUT ANY WARRANTY; without even the implied warranty of
-- MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
-- GNU Affero General Public License for more details.
--
-- You should have received a copy of the GNU Affero General Public License
-- along with this program.  If not, see <https://www.gnu.org/licenses/>.

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(255) UNIQUE NOT NULL,
    twitch_auth_code VARCHAR(255),
    twitch_auth_token VARCHAR(255),
    twitch_token_expiry BIGINT,
    twitch_token_refresh_code VARCHAR(255),
    twitch_user_id VARCHAR(255),
    twitch_user_name VARCHAR(255),
    twitch_redeem_id VARCHAR(255),
    eventsub_subscription_id VARCHAR(255),
    discord_user_id VARCHAR(255),
    discord_server_id VARCHAR(255),
    discord_auth_code VARCHAR(255)
);

-- Deduplicates Twitch EventSub webhook redeliveries.  Rows expire after
-- 10 minutes (matching Twitch's redelivery window) and are pruned on insert.
CREATE TABLE IF NOT EXISTS eventsub_messages (
    message_id VARCHAR(255) PRIMARY KEY,
    expires_at TIMESTAMP NOT NULL,
    INDEX idx_expires (expires_at)
);

CREATE TABLE IF NOT EXISTS redemptions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    streamer_session_id VARCHAR(255) NOT NULL,
    viewer_twitch_user_id VARCHAR(255) NOT NULL,
    viewer_twitch_user_name VARCHAR(255),
    invite_url VARCHAR(255),
    is_manual BOOLEAN DEFAULT FALSE,
    twitch_redemption_id VARCHAR(255),
    twitch_reward_id VARCHAR(255),
    redeemed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fulfilled_at TIMESTAMP NULL,
    revoked_at TIMESTAMP NULL,
    INDEX idx_viewer (viewer_twitch_user_id),
    INDEX idx_fulfilled (fulfilled_at),
    INDEX idx_streamer (streamer_session_id)
);
