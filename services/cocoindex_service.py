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

# Mirrors code_embedding_flow's LocalFile included_patterns/excluded_patterns
# below — kept as plain extensions/dir names here since we're walking the
# filesystem directly rather than going through CocoIndex's own source.
_INDEXABLE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".cpp", ".c", ".go", ".rs", ".md"}
_EXCLUDED_DIRS = {"node_modules", "__pycache__", "venv", ".git", "dist", "build"}


def _normalize_path(filename: str) -> str:
    """CocoIndex's LocalFile source returns OS-native path separators —
    backslashes on Windows. ast_service.py normalizes to forward-slash via
    Path.as_posix(), so CodeEmbedding.filename and CodeSymbol/CodeEdge
    filenames don't match on Windows unless normalized to the same form.
    Discovered via find_indexing_gaps() flagging every file as "missing"
    when they weren't — the real bug was this mismatch, not a CocoIndex
    failure."""
    return filename.replace("\\", "/")

class CocoIndexService:
    def __init__(self):
        # Lazily loaded to avoid blocking startup
        self._embedder = None
        self._pg_db_auth_ref = None

    def _get_embedder(self):
        if self._embedder is None:
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        return self._embedder
    
    def _get_pg_db_auth_ref(self):
        """CocoIndex's auth registry is process-global — add_auth_entry()
        raises RuntimeError('Auth entry already exists') if called twice
        with the same key in one process. Register once and reuse the
        reference for every subsequent index_codebase() call instead of
        re-registering per upload (discovered when a second upload in the
        same running server process failed indexing with this exact error)."""
        if self._pg_db_auth_ref is None:
            self._pg_db_auth_ref = cocoindex.add_auth_entry(
                "pg_db",
                cocoindex.DatabaseConnectionSpec(url=settings.COCOINDEX_DATABASE_URL)
            )
        return self._pg_db_auth_ref


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
                    database=self._get_pg_db_auth_ref()
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

    def find_indexing_gaps(self, project_id: str, codebase_path: str, db_session) -> list[str]:
        """Returns relative filenames that exist on disk (matching the same
        included_patterns/excluded_patterns as code_embedding_flow) but have
        zero CodeEmbedding rows for this project.

        CocoIndex writes directly to Postgres via its own Rust engine and
        logs row-level failures internally without raising a Python
        exception index_codebase() would propagate — so a project can
        reach status="ready" with some files silently unindexed. This is a
        best-effort, file-level check (not chunk-level): it can't detect a
        file that got *some* but not all of its chunks embedded, only a
        file with zero embedding rows at all."""
        from models.database import CodeEmbedding

        base = Path(codebase_path)
        expected_files = set()
        for root, dirs, files in os.walk(codebase_path):
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS and not d.startswith(".")]
            for fname in files:
                if os.path.splitext(fname)[1] in _INDEXABLE_EXTENSIONS:
                    rel_path = (Path(root) / fname).relative_to(base).as_posix()
                    expected_files.add(rel_path)

        indexed_files = {
            _normalize_path(row[0]) for row in
            db_session.query(CodeEmbedding.filename)
            .filter(CodeEmbedding.project_id == project_id)
            .distinct()
            .all()
        }

        return sorted(expected_files - indexed_files)

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
                "filename": _normalize_path(r.filename),
                "location": r.location,
                "code_text": r.code_text,
                "language": r.language
            }
            for r in results
        ]