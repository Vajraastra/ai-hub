"""Independent PoC server, using the Hub environment without launching other apps."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import webbrowser

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from anima_studio_routes import anima_studio_router, _tasks, _sessions

ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app):
    browser_task = None
    if getattr(app.state, "open_browser", False):
        async def open_browser():
            await asyncio.sleep(0.8)
            webbrowser.open(f"http://127.0.0.1:{app.state.port}")
        browser_task = asyncio.create_task(open_browser())
    try:
        yield
    finally:
        tasks = list(_tasks)
        if browser_task:
            tasks.append(browser_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await _sessions.shutdown()
        if _sessions._bridge is not None:
            await asyncio.to_thread(_sessions._bridge.stop_all_running)


api = FastAPI(title="Anima Studio · PoC", lifespan=lifespan)
api.include_router(anima_studio_router)
api.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@api.get("/")
@api.get("/anima-studio")
async def page():
    return FileResponse(ROOT / "static/anima_studio.html", headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9754)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    api.state.open_browser = args.open_browser
    api.state.port = args.port
    uvicorn.run(api, host="127.0.0.1", port=args.port)
