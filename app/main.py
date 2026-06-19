from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import os

from app.database import engine, Base
from app.api import router as api_router

# Ensure tables exist
Base.metadata.create_all(bind=engine)

# Initialize FastAPI App
app = FastAPI(
    title="Distributed Job Scheduler",
    description="A custom distributed task queue and priority scheduler.",
    version="1.0.0"
)

# Enable CORS for convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure app directories exist
os.makedirs("app/static", exist_ok=True)
os.makedirs("app/static/exports", exist_ok=True)
os.makedirs("app/templates", exist_ok=True)

# Mount static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

@app.get("/")
def read_root(request: Request):
    """Serves the Glassmorphism Dashboard page."""
    return templates.TemplateResponse("index.html", {"request": request})

# Register router
app.include_router(api_router)
