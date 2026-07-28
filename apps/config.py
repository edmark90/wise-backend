from dotenv import load_dotenv
import os

load_dotenv()

DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USERNAME = os.getenv("DB_USERNAME")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DATABASE = os.getenv("DB_DATABASE")
SECRET_KEY = os.getenv("SECRET_KEY", "wise-secret-key-change-in-production")