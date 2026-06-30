"""Atlas Files — PC file index, search, and policy-gated open/read."""
from atlas_files.access import find_file, open_path, read_file_path
from atlas_files.indexer import FileIndexer, default_index_roots

__all__ = [
    "FileIndexer",
    "default_index_roots",
    "find_file",
    "open_path",
    "read_file_path",
]
