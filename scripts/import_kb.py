import os
from pathlib import Path


knowledge_root = Path(os.environ.get("KNOWLEDGE_ROOT", "/data/support-agent-kb"))
for path in knowledge_root.rglob("*.md"):
    print(path)
