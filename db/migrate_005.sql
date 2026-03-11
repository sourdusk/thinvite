-- Rename ext_min_follow_days to ext_min_follow_minutes (column stores minutes, not days)
ALTER TABLE users CHANGE ext_min_follow_days ext_min_follow_minutes INT DEFAULT NULL;
