import uuid

class RAGService:
    def __init__(self, cocoindex_service, llm_service, db_session, ast_service=None):
        self.cocoindex = cocoindex_service
        self.llm = llm_service
        self.db = db_session
        self.ast_service = ast_service

    async def process_query(self, project_id: str, query: str, session_id: str, selected_files: list[str] = None, top_k: int = 10):
        # 1. Retrieve (Mocked call - assumes you implement the vector query here)
        relevant_code = await self.cocoindex.search_relevant_code(
            project_id=project_id, 
            query=query, 
            db_session=self.db,
            top_k=top_k
        )
        
        # 2. Filter
        if selected_files:
            relevant_code = [c for c in relevant_code if c['filename'] in selected_files]

        # being sent to the LLM, not from every file in the project.
        context_map = ""
        if self.ast_service and relevant_code:
            filenames = list({c['filename'] for c in relevant_code})
            context_map = self.ast_service.build_context_map(project_id, filenames, self.db)
            
        # 3. Get History
        history = self._get_conversation_history(session_id)
        
        # 4. Generate with Gemini
        response_text = await self.llm.generate_response(
            query=query,
            context_code=relevant_code,
            conversation_history=history,
            context_map=context_map
        )
        
        # 5. Save
        self._ensure_session_exists(session_id, project_id)
        self._save_message(session_id, "user", query, [])
        self._save_message(session_id, "assistant", response_text, [c['filename'] for c in relevant_code])
        
        return {
            "response": response_text,
            "relevant_code": relevant_code,
            "sources": [c['filename'] for c in relevant_code],
            "context_map": context_map
        }

    def _get_conversation_history(self, session_id: str, max_messages: int = 20):
        from models.database import Message

        # fetch only last max_messages — prevents context window overflow
        msgs = (
            self.db.query(Message)
            .filter(Message.session_id == session_id)
            .order_by(Message.created_at.desc())   # desc to get latest first
            .limit(max_messages)
            .all()
        )

        # reverse back to chronological order for Gemini
        msgs = list(reversed(msgs))

        return [{"role": m.role, "content": m.content} for m in msgs]

    def _ensure_session_exists(self, session_id: str, project_id: str):
        from models.database import ChatSession
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        stmt = (
            pg_insert(ChatSession)
            .values(
                id=session_id,
                project_id=project_id,
                title="New Chat",
                created_at=now,
                updated_at=now
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"updated_at": now}   # just bump updated_at on every query — marks session as active
            )
        )
        self.db.execute(stmt)
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