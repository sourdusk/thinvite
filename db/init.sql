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
    discord_user_id VARCHAR(255),
    discord_server_id VARCHAR(255),
    discord_auth_code VARCHAR(255)
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
