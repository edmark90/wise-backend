"""
Migration: 001_remove_mobile_update_feature.py
Removes the obsolete Mobile Update / Push Update feature from the database.
- Drops app_update_announcements table (and foreign keys)
- Drops app_versions table
- Deletes orphaned 'Mobile Update' test records from notifications and notification_reads
- Drops app_version and apk_url columns from notifications table
"""

import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import text
from apps.database import engine

def run_migration():
    print("[migration] Starting database cleanup for mobile update removal...")
    with engine.connect() as conn:
        # 1. Clean up notification_reads referencing Mobile Update notifications
        print("[migration] Cleaning up test notifications and read receipts...")
        conn.execute(text("""
            DELETE FROM notification_reads 
            WHERE notification_id IN (
                SELECT id FROM (
                    SELECT id FROM notifications WHERE notification_type = 'Mobile Update'
                ) AS tmp
            )
        """))
        
        # 2. Delete test Mobile Update notifications
        conn.execute(text("DELETE FROM notifications WHERE notification_type = 'Mobile Update'"))
        conn.commit()
        print("[migration] Removed test Mobile Update notification records.")

        # 3. Drop app_update_announcements table
        print("[migration] Dropping app_update_announcements table...")
        conn.execute(text("DROP TABLE IF EXISTS app_update_announcements"))
        conn.commit()
        print("[migration] Table 'app_update_announcements' dropped successfully.")

        # 4. Drop app_versions table
        print("[migration] Dropping app_versions table...")
        conn.execute(text("DROP TABLE IF EXISTS app_versions"))
        conn.commit()
        print("[migration] Table 'app_versions' dropped successfully.")

        # 5. Drop app_version and apk_url columns from notifications if present
        cols = [r[0] for r in conn.execute(text("SHOW COLUMNS FROM notifications")).fetchall()]
        if "app_version" in cols:
            print("[migration] Dropping column notifications.app_version...")
            conn.execute(text("ALTER TABLE notifications DROP COLUMN app_version"))
            conn.commit()
            print("[migration] Column 'app_version' dropped.")
            
        if "apk_url" in cols:
            print("[migration] Dropping column notifications.apk_url...")
            conn.execute(text("ALTER TABLE notifications DROP COLUMN apk_url"))
            conn.commit()
            print("[migration] Column 'apk_url' dropped.")

        print("[migration] Database cleanup completed successfully!")

if __name__ == "__main__":
    run_migration()

