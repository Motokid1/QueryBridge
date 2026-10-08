import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
load_dotenv(BACKEND / '.env')

@dataclass
class Settings:
    storage: Path = field(default_factory=lambda: Path(os.getenv('STORAGE_PATH',str(BACKEND/'storage'))))
    ollama_url: str = os.getenv('OLLAMA_URL','http://127.0.0.1:11434')
    model: str = os.getenv('MODEL','qwen2.5-coder:3b')
    eval_models: tuple = tuple(m.strip() for m in os.getenv('EVAL_MODELS','qwen2.5-coder:1.5b,qwen2.5-coder:3b').split(',') if m.strip())
    embed_model: str = os.getenv('EMBED_MODEL','nomic-embed-text')
    prompt_variant: str = 'original'
    temperature: float = float(os.getenv('TEMPERATURE','0'))
    seed: int = int(os.getenv('SEED','42'))
    max_rows: int = int(os.getenv('MAX_ROWS','200'))
    query_timeout_ms: int = int(os.getenv('QUERY_TIMEOUT_MS','5000'))
    llm_timeout: float = float(os.getenv('LLM_TIMEOUT_SECONDS','180'))
    max_attempts: int = int(os.getenv('MAX_ATTEMPTS','3'))
    num_ctx: int = int(os.getenv('NUM_CTX','4096'))
    num_gpu: int = int(os.getenv('NUM_GPU','0'))
    max_upload_bytes: int = int(os.getenv('MAX_UPLOAD_MB','25'))*1024*1024
    max_import_rows: int = int(os.getenv('MAX_IMPORT_ROWS','500000'))
    local_file_roots: tuple = tuple(Path(p).resolve() for p in os.getenv('LOCAL_FILE_ROOTS','').split(';') if p.strip())
    access_key: str = os.getenv('APP_ACCESS_KEY','')
    app_port: int = int(os.getenv('APP_PORT','8000'))
    origins: tuple = ('http://127.0.0.1:8000','http://localhost:8000','http://127.0.0.1:5173','http://localhost:5173')

    def __post_init__(self):
        if not 0<=self.temperature<=2:raise ValueError('TEMPERATURE must be between 0 and 2')
        self.storage=Path(self.storage).resolve()
        if not 1<=self.app_port<=65535:raise ValueError('Invalid application port')
        self.origins=(f'http://127.0.0.1:{self.app_port}',f'http://localhost:{self.app_port}','http://127.0.0.1:5173','http://localhost:5173')
        u=urlparse(self.ollama_url)
        if u.scheme!='http' or u.hostname not in {'localhost','127.0.0.1','::1'} or u.username or u.password or u.path not in {'','/'} or u.query or u.fragment:
            raise ValueError('Ollama must be a loopback HTTP origin')
        if not 1<=len(self.eval_models)<=4 or len(set(self.eval_models))!=len(self.eval_models):raise ValueError('Use 1–4 unique evaluation models')
        if any('cloud' in m.lower() for m in (self.model,self.embed_model,*self.eval_models)):
            raise ValueError('Cloud model names are not supported')
        if not 1<=self.max_rows<=1000 or not 1<=self.max_attempts<=3 or not 100<=self.query_timeout_ms<=30000:
            raise ValueError('Invalid query limits')
        if not 2048<=self.num_ctx<=8192 or not 10<=self.llm_timeout<=600 or not 1<=self.max_upload_bytes<=100*1024*1024:
            raise ValueError('Invalid model/upload limits')
        if not 1<=self.max_import_rows<=1000000: raise ValueError('Invalid import row limit')
        self.storage.mkdir(parents=True,exist_ok=True)
        for name in ['datasets','staging','embeddings']: (self.storage/name).mkdir(exist_ok=True)
        if not self.access_key:
            path=self.storage/'access.key'
            if not path.exists(): path.write_text(secrets.token_urlsafe(32),encoding='utf8')
            self.access_key=path.read_text(encoding='utf8').strip()
        if len(self.access_key)<24: raise ValueError('APP_ACCESS_KEY must contain at least 24 characters')

