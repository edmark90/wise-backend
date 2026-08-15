from dotenv import load_dotenv
import os

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USERNAME = os.getenv("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DATABASE = os.getenv("DB_DATABASE")
SECRET_KEY = os.getenv("SECRET_KEY", "wise-secret-key-change-in-production")
# Optional Firebase Admin service-account credentials for FCM push delivery.
# Two supported forms (either is enough):
#   - FIREBASE_CREDENTIALS_JSON: the full service-account JSON as a string
#     (recommended for Render/cloud — paste it into an env var).
#   - FIREBASE_CREDENTIALS_PATH: a path to the .json file on disk (local dev).
# When neither is set/missing, push is skipped gracefully and API-only sync
# still works.
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.getenv("MODEL_PATH", os.path.join(BASE_DIR, "apps", "ai_model", "waste_classifier.tflite"))
LABELS_PATH = os.getenv("LABELS_PATH", os.path.join(BASE_DIR, "apps", "ai_model", "labels.txt"))
CLASS_MAPPING_PATH = os.getenv("CLASS_MAPPING_PATH", os.path.join(BASE_DIR, "apps", "ai_model", "class_mapping.json"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/jpg"}

# Directory where captured waste photos are stored (served at /uploads/waste/...).
WASTE_UPLOAD_DIR = os.path.join(BASE_DIR, "uploads", "waste")
