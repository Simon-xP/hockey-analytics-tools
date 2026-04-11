"""FastAPI backend for the hockey analytics frontend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import dashboard, players, yahoo, news, goalie_matchups

app = FastAPI(title="PuckAgent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(players.router, prefix="/api/players", tags=["players"])
app.include_router(yahoo.router, prefix="/api/yahoo", tags=["yahoo"])
app.include_router(news.router, prefix="/api/news", tags=["news"])
app.include_router(goalie_matchups.router, prefix="/api/goalie-matchups", tags=["goalie-matchups"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
