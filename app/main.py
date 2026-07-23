from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.services.seed import seed_builtin_personas
from app.web.routes import accounts, channels, dashboard, logs, personas, settings

app = FastAPI(title="Diablogram AI — нейрокомментинг")

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

app.include_router(dashboard.router)
app.include_router(channels.router)
app.include_router(accounts.router)
app.include_router(personas.router)
app.include_router(settings.router)
app.include_router(logs.router)


@app.on_event("startup")
async def on_startup() -> None:
    await seed_builtin_personas()
