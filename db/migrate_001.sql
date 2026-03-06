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

-- Migration 001: webhook-based EventSub + channel-point redemption tracking
-- Run once against any DB initialised from a pre-2026-03 init.sql.
-- All statements are idempotent (IF NOT EXISTS / IF EXISTS).

-- users: store the Twitch EventSub subscription ID
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS eventsub_subscription_id VARCHAR(255);

-- redemptions: track the Twitch-side redemption + reward IDs (needed for
-- cancel/fulfill API calls) and flag manually-added rows.
ALTER TABLE redemptions
    ADD COLUMN IF NOT EXISTS twitch_redemption_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS twitch_reward_id     VARCHAR(255),
    ADD COLUMN IF NOT EXISTS is_manual            BOOLEAN DEFAULT FALSE;

-- Deduplicates Twitch EventSub webhook redeliveries.
CREATE TABLE IF NOT EXISTS eventsub_messages (
    message_id VARCHAR(255) PRIMARY KEY,
    expires_at TIMESTAMP NOT NULL,
    INDEX idx_expires (expires_at)
);
