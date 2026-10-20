"""Настройки читаются только из окружения; repr не раскрывает credentials."""
from dataclasses import dataclass, field
import json
import os

@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv('DATABASE_URL', ''), repr=False)
    api_token: str = field(default_factory=lambda: os.getenv('APP_API_TOKEN', ''), repr=False)
    backend: str = field(default_factory=lambda: os.getenv('LLM_BACKEND', 'openrouter'))
    model: str = field(default_factory=lambda: os.getenv('OPENROUTER_MODEL', ''))
    llm_key: str = field(default_factory=lambda: os.getenv('OPENROUTER_API_KEY', ''), repr=False)
    structured_mode: str = field(default_factory=lambda: os.getenv('LLM_STRUCTURED_MODE', 'json_object'))
    op_url: str = field(default_factory=lambda: os.getenv('OPENPROJECT_URL', 'http://openproject:8080'))
    op_public_url: str = field(default_factory=lambda: os.getenv('OPENPROJECT_PUBLIC_URL', 'http://localhost:8080'))
    pm_token: str = field(default_factory=lambda: os.getenv('OPENPROJECT_PM_TOKEN', ''), repr=False)
    sa_token: str = field(default_factory=lambda: os.getenv('OPENPROJECT_SA_TOKEN', ''), repr=False)
    project_id: int = field(default_factory=lambda: int(os.getenv('OPENPROJECT_PROJECT_ID', '3')))
    book_url: str = field(default_factory=lambda: os.getenv('BOOKSTACK_URL', 'http://bookstack'))
    book_public_url: str = field(default_factory=lambda: os.getenv('BOOKSTACK_PUBLIC_URL', 'http://localhost:8082'))
    book_token_id: str = field(default_factory=lambda: os.getenv('BOOKSTACK_RUNTIME_TOKEN_ID', ''), repr=False)
    book_token_secret: str = field(default_factory=lambda: os.getenv('BOOKSTACK_RUNTIME_TOKEN_SECRET', ''), repr=False)
    book_id: int = field(default_factory=lambda: int(os.getenv('BOOKSTACK_BOOK_ID', '1')))
    agent_ids: dict = field(default_factory=lambda: json.loads(os.getenv('AGENT_USER_IDS') or '{}'))
    status_ids: dict = field(default_factory=lambda: json.loads(os.getenv('OPENPROJECT_STATUS_IDS') or '{}'))
    max_calls: int = 8
    max_tokens: int = 60000
    max_cost: float = 1.0
    completion_tokens: int = 2048
    input_price: float = field(default_factory=lambda: float(os.getenv('LLM_INPUT_USD_PER_MILLION', '1')))
    output_price: float = field(default_factory=lambda: float(os.getenv('LLM_OUTPUT_USD_PER_MILLION', '4')))

settings = Settings()
