import psycopg2

try:
    conn = psycopg2.connect(
        dbname="coding_agent",
        user="postgres",
        password="Kaushal123",
        host="localhost",
        port="5432"
    )
    conn.autocommit = True
    cursor = conn.cursor()
    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    print("pgvector extension enabled successfully.")
    cursor.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
