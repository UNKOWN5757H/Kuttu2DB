from fastapi import FastAPI
import os

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Bot is Alive"}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

# Optional: Add a simple '/ready' that checks for env var like BOT_TOKEN to confirm readiness
@app.get('/ready')
async def ready():
    token = os.environ.get('BOT_TOKEN')
    if token:
        return {"ready": True}
    return {"ready": False}
