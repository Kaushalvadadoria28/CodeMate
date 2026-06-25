from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from datetime import datetime

# Common Response Wrapper
class APIResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

# Chat Schemas
class ChatRequest(BaseModel):
    message: str
    project_id: str
    session_id: str
    selected_files: Optional[List[str]] = None

class SessionSaveRequest(BaseModel):
    session_id: str
    project_id: str
    title: str

# Project Schemas
class ProjectResponse(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    files_count: int

    class Config:
        from_attributes = True