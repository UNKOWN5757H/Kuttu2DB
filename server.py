from fastapi import FastAPI
import os

app = FastAPI()

@app.get('/')
async def root():
    return {"status": "ok", "message": "Bot is Alive"}

@app.get('/healthz')
async def healthz():
    return {"status": "ok"}

@app.get('/ready')
async def ready():
    token = os.environ.get('BOT_TOKEN')
    return {"ready": bool(token)}
