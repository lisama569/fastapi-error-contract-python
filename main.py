"""根入口，兼容 `uvicorn main:app`。实际装配见 app.main。"""

from app.main import app

__all__ = ["app"]
