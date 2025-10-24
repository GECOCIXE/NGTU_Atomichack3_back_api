from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
from scripts.crud import SECRET_KEY, ALGORITHM
from routers import auth, upload, history, result, download, decisions, requirements_stats, process_analysis, export_csv, admin_panel, errors
from scripts.models import Base
from scripts.db import engine

load_dotenv()

app = FastAPI()

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_PATHS = {
    "/login",
    "/reg"
}

def _strip_bearer(auth_header: str | None):
    if not auth_header:
        return None
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return None

@app.middleware("http")
async def jwt_auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    raw = request.headers.get("Authorization")
    token = _strip_bearer(raw)
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Authorization header is missing or invalid"})

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        exp = payload.get("exp")
        if exp is not None and datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(tz=timezone.utc):
            return JSONResponse(status_code=401, content={"detail": "Token expired"})
        username: str = payload.get("sub")
        if username is None:
            raise JWTError("No sub in token")
        request.state.user = username
    except JWTError:
        return JSONResponse(status_code=401, content={"detail": "Token is invalid or expired"})

    return await call_next(request)

app.include_router(auth.router)
app.include_router(upload.router)
app.include_router(history.router)
app.include_router(result.router)
app.include_router(download.router)
app.include_router(decisions.router)
app.include_router(requirements_stats.router)
app.include_router(process_analysis.router)
app.include_router(errors.router)
app.include_router(export_csv.router)
app.include_router(admin_panel.router)