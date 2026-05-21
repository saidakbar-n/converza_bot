from fastapi import FastAPI
from routers import webhooks

app = FastAPI(title="Converza Telegram Bot", version="0.1.0")

app.include_router(webhooks.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
