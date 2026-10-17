import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_local_settings():
    """Load server-side credentials without printing them or sending them to the browser."""
    path = ROOT / '.env.local'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                name, value = line.split('=', 1)
                os.environ.setdefault(name.strip(), value.strip())

def llm_ready():
    return all(os.environ.get(key) for key in ('YUKI_LLM_BASE_URL', 'YUKI_LLM_API_KEY', 'YUKI_LLM_MODEL'))
