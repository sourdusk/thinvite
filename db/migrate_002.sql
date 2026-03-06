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

-- Migration 002: distinguish auto-expired redemptions from manually-revoked ones.
-- Run once after migrate_001.sql.  Idempotent (IF NOT EXISTS).

-- redemptions: flag rows that were auto-cancelled by the 24-hour expiry loop,
-- as opposed to being manually revoked by the streamer.  Both paths set
-- revoked_at; this column lets the dashboard show them as "Expired" vs "Revoked".
ALTER TABLE redemptions
    ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT FALSE;
