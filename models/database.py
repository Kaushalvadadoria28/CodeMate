from sqlalchemy import Column, String, Text, Integer, DateTime, JSON, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

Base = declarative_base()

class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String, default='indexing')
    files_count = Column(Integer, default=0)

class CodeEmbedding(Base):
    __tablename__ = "code_embeddings"
    project_id = Column(String, primary_key=True)
    filename = Column(String, primary_key=True)
    location = Column(String, primary_key=True)
    code_text = Column(Text)
    embedding = Column(Vector(384)) # Dimension for all-MiniLM-L6-v2
    language = Column(String)

class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(String, primary_key=True)
    project_id = Column(String, ForeignKey("projects.id"))
    title = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("chat_sessions.id"))
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    context_files = Column(JSON)
    session = relationship("ChatSession", back_populates="messages")