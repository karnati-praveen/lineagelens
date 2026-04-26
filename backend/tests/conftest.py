import os


os.environ["APP_ENV"] = "test"
os.environ["BACKEND_MODE"] = "team"
os.environ["NEO4J_ENABLED"] = "false"
os.environ["VECTOR_SEARCH_ENABLED"] = "false"
os.environ["LINEAGE_STRICT_MODE"] = "false"
os.environ["BACKEND_CORS_ORIGINS"] = "http://localhost:3000"
os.environ["JWT_SECRET_KEY"] = "pytest-jwt-secret-key-abcdefghijklmnopqrstuvwxyz0123456789"
os.environ["JWT_REFRESH_SECRET_KEY"] = "pytest-refresh-secret-key-0123456789abcdefghijklmnopqrstuvwxyz"
