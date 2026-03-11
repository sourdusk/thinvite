-- Add source column to redemptions (channel_points, follow_age, manual)
ALTER TABLE redemptions ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL DEFAULT 'channel_points';

-- Add extension config columns to users
ALTER TABLE users ADD COLUMN IF NOT EXISTS ext_min_follow_minutes INT DEFAULT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS ext_cooldown_days INT DEFAULT NULL;
