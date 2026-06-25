import shutil
import uuid
import zipfile
import os
import asyncio
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from config import settings
from models.database import Base, Project, ChatSession, CodeEmbedding
from models.schemas import APIResponse, ChatRequest, SessionSaveRequest
from services.cocoindex_service import CocoIndexService
from services.llm_service import LLMService
from services.rag_service import RAGService

# --- Database Setup ---
engine = create_engine(settings.DATABASE_URL)
Base.metadata.create_all(bind=engine)

def get_db():
    db = Session(bind=engine)
    try:
        yield db
    finally:
        db.close()

# --- Service Initialization ---
coco_service = CocoIndexService()
llm_service = LLMService(
    api_key=settings.GEMINI_API_KEY,
    model_name=settings.GEMINI_MODEL
)

app = FastAPI(title="AI Coding Agent API (Gemini Powered)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "data": None,
            "error": {
                "message": str(exc.detail),
                "code": f"HTTP_{exc.status_code}_ERROR"
            }
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "data": None,
            "error": {
                "message": "Invalid request parameters",
                "details": exc.errors(),
                "code": "VALIDATION_ERROR"
            }
        }
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "data": None,
            "error": {
                "message": str(exc),
                "code": "INTERNAL_SERVER_ERROR"
            }
        }
    )

# --- Background Worker ---
async def process_codebase_task(project_id: str, file_path: str, db: Session):
    try:
        extract_path = settings.UPLOAD_DIR / project_id
        
        # 1. Extract
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
            
        # 2. Update DB Stats
        file_count = sum([len(files) for r, d, files in os.walk(extract_path)])
        
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.files_count = file_count
            db.commit()

        # 3. Index with CocoIndex
        await coco_service.index_codebase(project_id, str(extract_path))
        
        # 4. Finish
        if project:
            project.status = "ready"
            db.commit()

            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Indexing Error: {e}")
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.status = "error"
            db.commit()
    finally:
        # Cleanup Zip
        if os.path.exists(file_path):
            os.remove(file_path)

# --- Routes ---

@app.post("/api/upload-codebase", response_model=APIResponse)
async def upload_codebase(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    project_id = str(uuid.uuid4())
    file_location = settings.UPLOAD_DIR / f"{project_id}.zip"
    
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    new_project = Project(
        id=project_id, 
        name=file.filename, 
        status="indexing"
    )
    db.add(new_project)
    db.commit()
    
    # Run indexing synchronously (blocking the API response until complete)
    await process_codebase_task(project_id, str(file_location), db)
    return APIResponse(success=True, data={"project_id": project_id, "status": "ready"})

@app.get("/api/indexing-status/{project_id}", response_model=APIResponse)
async def get_indexing_status(project_id: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return APIResponse(success=True, data={
        "status": project.status,
        "files_count": project.files_count
    })

@app.post("/api/chat", response_model=APIResponse)
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    rag_service = RAGService(coco_service, llm_service, db)
    result = await rag_service.process_query(
        project_id=request.project_id,
        query=request.message,
        session_id=request.session_id,
        selected_files=request.selected_files
    )
    return APIResponse(success=True, data=result)

@app.post("/api/session/save", response_model=APIResponse)
async def save_session(request: SessionSaveRequest, db: Session = Depends(get_db)):
    session = db.query(ChatSession).filter(ChatSession.id == request.session_id).first()
    if not session:
        session = ChatSession(id=request.session_id, project_id=request.project_id, title=request.title)
        db.add(session)
    else:
        session.title = request.title
    db.commit()
    return APIResponse(success=True, data={"session_id": session.id})

@app.get("/api/sessions/{project_id}", response_model=APIResponse)
async def get_sessions(project_id: str, db: Session = Depends(get_db)):
    sessions = db.query(ChatSession).filter(ChatSession.project_id == project_id).all()
    data = [{"id": s.id, "title": s.title, "created_at": s.created_at} for s in sessions]
    return APIResponse(success=True, data=data)