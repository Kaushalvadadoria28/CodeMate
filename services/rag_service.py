import uuid

class RAGService:
    def __init__(self, cocoindex_service, llm_service, db_session):
        self.cocoindex = cocoindex_service
        self.llm = llm_service
        self.db = db_session

    async def process_query(self, project_id: str, query: str, session_id: str, selected_files: list[str] = None):
        # 1. Retrieve (Mocked call - assumes you implement the vector query here)
        relevant_code = await self.cocoindex.search_relevant_code(
            project_id=project_id, 
            query=query, 
            db_session=self.db,
            top_k=10
        )
        
        # 2. Filter
        if selected_files:
            relevant_code = [c for c in relevant_code if c['filename'] in selected_files]
            
        # 3. Get History
        history = self._get_conversation_history(session_id)
        
        # 4. Generate with Gemini
        response_text = await self.llm.generate_response(
            query=query,
            context_code=relevant_code,
            conversation_history=history
        )
        
        # 5. Save
        self._ensure_session_exists(session_id, project_id)
        self._save_message(session_id, "user", query, [])
        self._save_message(session_id, "assistant", response_text, [c['filename'] for c in relevant_code])
        
        return {
            "response": response_text,
            "relevant_code": relevant_code,
            "sources": [c['filename'] for c in relevant_code]
        }

    def _get_conversation_history(self, session_id: str):
        from models.database import Message
        msgs = self.db.query(Message).filter(Message.session_id == session_id).order_by(Message.created_at).all()
        return [{"role": m.role, "content": m.content} for m in msgs]

    def _ensure_session_exists(self, session_id: str, project_id: str):
        from models.database import ChatSession
        session = self.db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if not session:
            new_session = ChatSession(
                id=session_id,
                project_id=project_id,
                title="New Chat"
            )
            self.db.add(new_session)
            self.db.commit()

    def _save_message(self, session_id: str, role: str, content: str, context_files: list):
        from models.database import Message
        new_msg = Message(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            context_files=context_files
        )
        self.db.add(new_msg)
        self.db.commit()