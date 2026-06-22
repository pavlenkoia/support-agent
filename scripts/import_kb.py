from pathlib import Path

knowledge_root = Path('/home/tian/support-agent-kb')
for path in knowledge_root.rglob('*.md'):
    print(path)
