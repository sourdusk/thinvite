-- migrate_003: add indexes for frequently queried columns
--
-- users.twitch_user_id  — used by get_user_by_twitch_id() on every EventSub
--                         event and during bot subscription recovery at startup.
-- redemptions.twitch_redemption_id — used by expiry loop and fulfillment logic.

ALTER TABLE users
  ADD INDEX idx_twitch_user_id (twitch_user_id);

ALTER TABLE redemptions
  ADD INDEX idx_twitch_redemption_id (twitch_redemption_id);
