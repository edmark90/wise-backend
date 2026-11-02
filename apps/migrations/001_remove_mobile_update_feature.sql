-- Migration: 001_remove_mobile_update_feature.sql
-- Removes the obsolete Mobile Update / Push Update feature from the database.

-- 1. Remove notification read receipts for Mobile Update notifications
DELETE FROM notification_reads 
WHERE notification_id IN (
    SELECT id FROM (
        SELECT id FROM notifications WHERE notification_type = 'Mobile Update'
    ) AS tmp
);

-- 2. Delete test Mobile Update notifications
DELETE FROM notifications WHERE notification_type = 'Mobile Update';

-- 3. Drop obsolete tables
DROP TABLE IF EXISTS app_update_announcements;
DROP TABLE IF EXISTS app_versions;

-- 4. Drop obsolete columns from notifications table
ALTER TABLE notifications DROP COLUMN IF EXISTS app_version;
ALTER TABLE notifications DROP COLUMN IF EXISTS apk_url;

