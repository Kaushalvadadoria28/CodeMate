import zipfile
import os

MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB
MAX_FILE_COUNT = 10000                        # prevent zip bombs with many small files

ALLOWED_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".java", ".cpp", ".c", ".go", ".rs",
    ".md", ".txt", ".json", ".yaml", ".yml",
    ".html", ".css", ".env", ".toml", ".cfg", ".ini"
}

def validate_zip(zip_path: str, extract_to: str):
    """
    Validates zip file before extraction.
    Raises ValueError if any check fails.
    """
    with zipfile.ZipFile(zip_path, 'r') as zf:
        entries = zf.infolist()

        # 1. file count check — catches zip bombs with many tiny files
        if len(entries) > MAX_FILE_COUNT:
            raise ValueError(f"Zip contains too many files ({len(entries)}). Maximum allowed is {MAX_FILE_COUNT}.")

        # 2. uncompressed size check — catches zip bombs
        total_size = sum(e.file_size for e in entries)
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"Zip uncompressed size ({total_size / 1024 / 1024:.1f} MB) "
                f"exceeds limit of {MAX_UNCOMPRESSED_BYTES / 1024 / 1024:.0f} MB."
            )

        # 3. path traversal check — catches malicious zips with ../../../etc/passwd entries
        real_extract_to = os.path.realpath(extract_to)
        for entry in entries:
            target_path = os.path.realpath(
                os.path.join(real_extract_to, entry.filename)
            )
            if not target_path.startswith(real_extract_to + os.sep):
                raise ValueError(f"Path traversal detected in zip entry: {entry.filename}")

        # 4. extension allowlist — skip binaries and executables
        for entry in entries:
            if entry.is_dir():
                continue
            ext = os.path.splitext(entry.filename)[1].lower()
            if ext and ext not in ALLOWED_EXTENSIONS:
                raise ValueError(f"Disallowed file type in zip: {entry.filename}")