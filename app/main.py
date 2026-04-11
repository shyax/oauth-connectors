import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.auth.router import router as auth_router
from app.observability.logging import configure_logging, new_correlation_id, clear_context
from app.webhooks.slack import router as webhooks_router

configure_logging()

app = FastAPI(title="OAuth Integration Platform", version="0.1.0")


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID") or new_correlation_id()
    response: Response = await call_next(request)
    response.headers["X-Correlation-ID"] = cid
    clear_context()
    return response


app.include_router(auth_router)
app.include_router(webhooks_router)


@app.get("/health")
def health():
    return {"status": "ok"}
