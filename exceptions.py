class ProjectNotFoundError(Exception):
    def __init__(self, project_id: str):
        self.project_id = project_id
        super().__init__(f"Project {project_id} not found")

class SessionNotFoundError(Exception):
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session {session_id} not found")

class ProjectNotReadyError(Exception):
    def __init__(self, project_id: str, status: str):
        self.project_id = project_id
        self.status = status
        super().__init__(f"Project {project_id} is not ready (status: {status})")

class SymbolNotFoundError(Exception):
    def __init__(self, project_id: str, filename: str, symbol_name: str):
        self.project_id = project_id
        self.filename = filename
        self.symbol_name = symbol_name
        super().__init__(f"Symbol '{symbol_name}' not found in {filename} for project {project_id}")