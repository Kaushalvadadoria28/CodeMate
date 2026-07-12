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

# Directories whose contents should never be extracted, regardless of
# extension — build artifacts and caches, not source.
EXCLUDED_DIR_SEGMENTS = {"__pycache__", ".git", "node_modules", "venv", ".venv", "dist", "build"}


def _is_in_excluded_dir(filename: str) -> bool:
    parts = filename.replace("\\", "/").split("/")
    return any(p in EXCLUDED_DIR_SEGMENTS for p in parts)


def validate_zip(zip_path: str, extract_to: str) -> list[str]:
    """
    Validates zip file before extraction and returns the list of member
    names that are safe to extract.

    Hard-fails the whole upload (raises ValueError) for structural risks:
    file count, uncompressed size, path traversal — these indicate a
    malicious or corrupt archive, not just an irrelevant file.

    Disallowed-extension files and excluded directories (__pycache__, .git,
    node_modules, etc.) are silently excluded from the returned list rather
    than failing the upload — one stray .log or .pyc file in an otherwise
    valid codebase should not block indexing.
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

        # 4. extension allowlist + excluded dirs — skip, don't fail
        safe_members = []
        skipped = []
        for entry in entries:
            if entry.is_dir():
                safe_members.append(entry.filename)
                continue

            if _is_in_excluded_dir(entry.filename):
                skipped.append(entry.filename)
                continue

            ext = os.path.splitext(entry.filename)[1].lower()
            if not ext or ext in ALLOWED_EXTENSIONS:
                safe_members.append(entry.filename)
            else:
                skipped.append(entry.filename)

        if skipped:
            print(f"Zip validation: skipping {len(skipped)} disallowed/excluded file(s): {skipped[:10]}"
                  f"{' ...' if len(skipped) > 10 else ''}")

        return safe_members