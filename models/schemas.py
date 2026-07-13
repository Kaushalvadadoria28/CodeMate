from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime

# Common Response Wrapper
class APIResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

# Chat Schemas
class ChatRequest(BaseModel):
    message: str
    project_id: str
    session_id: str
    selected_files: Optional[List[str]] = None
    top_k: int = Field(default=10, ge=1, le=30)  # added for phase 3

class SessionSaveRequest(BaseModel):
    session_id: str
    project_id: str
    title: str

# Session Schemas — ADD THESE
class SessionResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int

    class Config:
        from_attributes = True

class PaginatedSessionsResponse(BaseModel):
    sessions: List[SessionResponse]
    total: int
    limit: int
    offset: int
    has_more: bool

class MessageResponse(BaseModel):
    id: str
    session_id: str
    role: str        # "user" | "assistant"
    content: str
    created_at: datetime
    context_files: Optional[List[str]] = None

    class Config:
        from_attributes = True

class PaginatedMessagesResponse(BaseModel):
    messages: List[MessageResponse]
    total: int
    limit: int
    offset: int
    has_more: bool

# Project Schemas
class ProjectResponse(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    files_count: int

    class Config:
        from_attributes = True

class SymbolResponse(BaseModel):
    filename: str
    symbol_name: str
    symbol_type: str
    start_line: int
    end_line: int

    class Config:
        from_attributes = True


class ContextMapResponse(BaseModel):
    project_id: str
    filenames: List[str]
    context_map: str
    edge_count: int

class OrphanSymbolResponse(BaseModel):
    filename: str
    symbol_name: str
    symbol_type: str
    start_line: int
    end_line: int

    class Config:
        from_attributes = True


class OrphanReportResponse(BaseModel):
    project_id: str
    total_symbols: int
    excluded_count: int
    orphan_count: int
    orphans: List[OrphanSymbolResponse]