# AI Coding Agent Backend

A Retrieval-Augmented Generation (RAG) backend powered by FastAPI, PostgreSQL (with pgvector), CocoIndex, and Google Gemini. This service allows you to upload a codebase `.zip` file, index it semantically, and have an AI chat with it to answer questions about the code.

## Project Structure

```text
├── backend/                   # FastAPI source code
│   ├── codingagent/           # Virtual environment (ignored in Git)
│   ├── models/                # SQLAlchemy database models & schemas
│   ├── services/              # Core business logic (CocoIndex, LLM, RAG)
│   ├── .env                   # Configuration secrets (ignored in Git)
│   ├── .env.example           # Template for environment configuration
│   ├── main.py                # FastAPI entry point
│   ├── requirements.txt       # Python dependencies
│   └── setup_project.py       # Helper script to bootstrap directories
├── pgvector/                  # Postgres vector extension (ignored in Git)
├── uploads/                   # Temporary folder for codebase ZIP extraction (ignored in Git)
├── README.md                  # Main project overview
└── api_documentation.md       # API endpoint details & response structures
```

## Setup & Installation

### 1. Prerequisites
- **Python 3.10+**
- **PostgreSQL** with the `pgvector` extension installed.

### 2. Configure Database & Extensions
Ensure your local PostgreSQL instance is running, and create a database named `coding_agent`:
```sql
CREATE DATABASE coding_agent;
```
Connect to your database and enable the vector extension:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### 3. Environment Configuration
Go into the `backend/` directory, copy the example environment file, and fill in your keys:
```bash
cd backend
cp .env.example .env
```
Open the `.env` file and set:
- `DATABASE_URL` and `COCOINDEX_DATABASE_URL` with your local PostgreSQL credentials.
- `GEMINI_API_KEY` with your actual Google Gemini API key.

### 4. Install Dependencies
Create a virtual environment and install the required Python libraries:
```bash
# Using standard venv (named codingagent as configured)
python -m venv codingagent
source codingagent/Scripts/activate  # On Windows: .\codingagent\Scripts\activate
pip install -r requirements.txt
```

### 5. Running the Application
Run the FastAPI development server:
```bash
uvicorn main:app --reload
```
The server will start at `http://127.0.0.1:8000`. You can visit `http://127.0.0.1:8000/docs` to view the interactive Swagger API documentation.

## API Documentation

For a detailed breakdown of all endpoint payloads, status flows, and response examples, refer to [api_documentation.md](api_documentation.md).
