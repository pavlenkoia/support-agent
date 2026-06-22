from pathlib import Path


def list_markdown_files(root: str) -> list[str]:
    return [str(path) for path in Path(root).rglob('*.md')]
