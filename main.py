import shutil
import uuid
import zipfile
import os
import asyncio
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, BackgroundTasks, Request, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

from config import settings
from models.database import Base, Project, ChatSession, CodeEmbedding, Message
from models.schemas import APIResponse, ChatRequest, SessionSaveRequest, SessionResponse, PaginatedSessionsResponse, MessageResponse, PaginatedMessagesResponse
from services.cocoindex_service import CocoIndexService
from services.llm_service import LLMService
from services.rag_service import RAGService

# --- Database Setup ---
engine = create_engine(settings.DATABASE_URL,
                        pool_size=10,          # max persistent connections kept in the pool
                        max_overflow=20,       # extra connections allowed under burst load (beyond pool_size)
                        pool_pre_ping=True,    # test connection health before using it (handles DB restarts)
                        pool_timeout=30,       # seconds to wait for a connection before raising an error
                        pool_recycle=1800,     # recycle connections after 30 min (prevents stale connection errors
    )
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
async def process_codebase_task(project_id: str, file_path: str):
    # Open a dedicated session for this background task —
    # do NOT reuse the request-scoped session, it will already be closed.
    db = Session(bind=engine)
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
        print(f"Indexing Error for project {project_id}: {e}")

        try:
            # Clean up any partial embeddings CocoIndex may have written
            deleted = db.query(CodeEmbedding).filter(
                CodeEmbedding.project_id == project_id
            ).delete(synchronize_session=False)
            print(f"Cleaned up {deleted} partial embedding rows for project {project_id}")

            # Mark project as error so frontend knows
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                project.status = "error"

            db.commit()

        except Exception as cleanup_error:
            # If cleanup itself fails, rollback and log — don't let this swallow the original error
            print(f"Cleanup failed for project {project_id}: {cleanup_error}")
            db.rollback()

    finally:
        db.close()
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

    # Schedule indexing to run after the response is sent — non-blocking
    background_tasks.add_task(process_codebase_task, project_id, str(file_location))

    return APIResponse(success=True, data={"project_id": project_id, "status": "indexing"})

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
        selected_files=request.selected_files,
        top_k=request.top_k 
    )
    return APIResponse(success=True, data=result)

@app.post("/api/session/save", response_model=APIResponse)
async def save_session(
    request: SessionSaveRequest,
    db: Session = Depends(get_db)
):
    # project existence check
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        return APIResponse(success=False, error=f"Project {request.project_id} not found")

    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    stmt = (
        pg_insert(ChatSession)
        .values(
            id=request.session_id,
            project_id=request.project_id,
            title=request.title,
            created_at=now,
            updated_at=now
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "title": request.title,
                "updated_at": now
            }
        )
    )
    db.execute(stmt)
    db.commit()

    session = db.query(ChatSession).filter(ChatSession.id == request.session_id).first()

    return APIResponse(success=True, data=SessionResponse(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at or session.created_at,
        message_count=len(session.messages)
    ).model_dump())

@app.get("/api/sessions/{project_id}", response_model=APIResponse)
async def get_sessions(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="updated_at", enum=["updated_at", "created_at"]),
    db: Session = Depends(get_db)
):
    # project existence check
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return APIResponse(success=False, error=f"Project {project_id} not found")

    base_query = db.query(ChatSession).filter(ChatSession.project_id == project_id)
    total = base_query.count()

    sort_column = ChatSession.updated_at if sort_by == "updated_at" else ChatSession.created_at

    sessions = (
        base_query
        .order_by(sort_column.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    data = PaginatedSessionsResponse(
        sessions=[
            SessionResponse(
                id=s.id,
                title=s.title,
                created_at=s.created_at,
                updated_at=s.updated_at or s.created_at,
                message_count=len(s.messages)
            )
            for s in sessions
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total
    )

    return APIResponse(success=True, data=data.model_dump())

@app.get("/api/sessions/{session_id}/messages", response_model=APIResponse)
async def get_messages(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db)
):
    # session existence check
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        return APIResponse(success=False, error=f"Session {session_id} not found")

    base_query = db.query(Message).filter(Message.session_id == session_id)
    total = base_query.count()

    messages = (
        base_query
        .order_by(Message.created_at.asc())   # asc — chronological order for chat
        .offset(offset)
        .limit(limit)
        .all()
    )

    data = PaginatedMessagesResponse(
        messages=[
            MessageResponse(
                id=m.id,
                session_id=m.session_id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                context_files=m.context_files
            )
            for m in messages
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total
    )

    return APIResponse(success=True, data=data.model_dump())