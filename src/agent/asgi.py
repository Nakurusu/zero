"""Server entry: gunicorn agent.asgi:app / uvicorn agent.asgi:app."""
from .api import create_app
from .config import Settings

app = create_app(Settings.from_env())
