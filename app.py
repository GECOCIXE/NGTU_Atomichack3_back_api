# app.py
# (Updated file: complete setup with routers, dependencies, and middleware)

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
from scripts.crud import SECRET_KEY, ALGORITHM
from routers import auth, upload, history, result, download
from scripts.models import Base
from scripts.db import engine

load_dotenv()

app = FastAPI()

# Create database tables
Base.metadata.create_all(bind=engine)

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public paths (no token required)
PUBLIC_PATHS = {
    "/auth/login",
    # Add more if needed, e.g., for registration
}

def _strip_bearer(auth_header: str | None):
    if not auth_header:
        return None
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):]
    return None

@app.middleware("http")
async def jwt_auth_middleware(request: Request, call_next):
    # Allow OPTIONS and public paths
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    raw = request.headers.get("Authorization")
    token = _strip_bearer(raw)
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Authorization header is missing or invalid"})

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        exp = payload.get("exp")
        if exp is not None:
            if datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(tz=timezone.utc):
                return JSONResponse(status_code=401, content={"detail": "Token expired"})
        request.state.user = payload.get("sub")
    except JWTError:
        return JSONResponse(status_code=401, content={"detail": "Token is invalid or expired"})

    return await call_next(request)

def get_current_user(request: Request = Depends()):
    user = request.state.user
    if not user:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    return user

# Include routers
app.include_router(auth.router, prefix="/auth")
app.include_router(upload.router)
app.include_router(history.router)
app.include_router(result.router)
app.include_router(download.router)