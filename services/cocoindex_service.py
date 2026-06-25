import cocoindex
import os
from pathlib import Path
from config import settings
from sentence_transformers import SentenceTransformer

def determine_language(extension: str) -> str:
    mapping = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "tsx", ".jsx": "javascript", ".java": "java",
        ".cpp": "cpp", ".c": "c", ".go": "go", ".rs": "rust"
    }
    return mapping.get(extension, "text")

def extract_extension(filename: str) -> str:
    return os.path.splitext(filename)[1]

class CocoIndexService:
    def __init__(self):
        # Lazily loaded to avoid blocking startup
        self._embedder = None

    def _get_embedder(self):
        if self._embedder is None:
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        return self._embedder

    async def index_codebase(self, project_id: str, codebase_path: str):
        """Index codebase using CocoIndex + Tree-sitter + Local Embeddings"""
        
        safe_project_id = project_id.replace('-', '_')
        @cocoindex.flow_def(name=f"CodeEmbedding_{safe_project_id}")
        def code_embedding_flow(flow_builder, data_scope):
            data_scope["files"] = flow_builder.add_source(
                cocoindex.sources.LocalFile(
                    path=codebase_path,
                    included_patterns=["*.py", "*.js", "*.jsx", "*.ts", "*.tsx", 
                                      "*.java", "*.cpp", "*.c", "*.go", "*.rs", "*.md"],
                    excluded_patterns=[".*", "node_modules", "__pycache__", 
                                      "venv", ".git", "dist", "build"]
                )
            )
            
            code_embeddings = data_scope.add_collector()
            
            # 2. Process Files
            with data_scope["files"].row() as file:
                
                # 3. Chunking (Generic Recursive Split)
                # We remove the custom language extractors to prevent DAG errors
                file["chunks"] = file["content"].transform(
                    cocoindex.functions.SplitRecursively(),
                    chunk_size=1500,
                    chunk_overlap=300
                )
                
                # 4. Embedding
                with file["chunks"].row() as chunk:
                    chunk["embedding"] = chunk["text"].transform(
                        cocoindex.functions.SentenceTransformerEmbed(
                            model="sentence-transformers/all-MiniLM-L6-v2"
                        )
                    )
                    
                    # 5. Collect Data
                    code_embeddings.collect(
                        project_id=project_id,
                        filename=file["filename"],
                        location=chunk["location"],
                        code_text=chunk["text"],
                        embedding=chunk["embedding"],
                        language="code"  # Default fallback
                    )
            
            code_embeddings.export(
                "code_embeddings",
                cocoindex.storages.Postgres(
                    table_name="code_embeddings",
                    database=cocoindex.add_auth_entry(
                        "pg_db",
                        cocoindex.DatabaseConnectionSpec(
                            url=settings.COCOINDEX_DATABASE_URL
                        )
                    )
                ),
                primary_key_fields=["project_id", "filename", "location"],
                vector_indexes=[
                    cocoindex.VectorIndexDef(
                        field_name="embedding",
                        metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY
                    )
                ]
            )

        # Ensure required tables and configurations exist
        await code_embedding_flow.setup_async()
        await code_embedding_flow.update_async()
        return True

    async def search_relevant_code(self, project_id: str, query: str, db_session, top_k: int = 5):
        from models.database import CodeEmbedding
        import asyncio
        
        # Embed query text (run in executor to avoid blocking the async event loop)
        embedder = self._get_embedder()
        query_embedding = await asyncio.to_thread(embedder.encode, query)
        query_embedding_list = query_embedding.tolist()

        # Perform exact Nearest Neighbor vector search via pgvector cosine distance
        results = db_session.query(CodeEmbedding).filter(
            CodeEmbedding.project_id == project_id
        ).order_by(
            CodeEmbedding.embedding.cosine_distance(query_embedding_list)
        ).limit(top_k).all()

        return [
            {
                "filename": r.filename,
                "location": r.location,
                "code_text": r.code_text,
                "language": r.language
            }
            for r in results
        ]