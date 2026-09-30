from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from workbench.models import RunStatus


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str
    repo_path: str
    task_type: Literal["review", "test_triage", "docs"]


class RunIn(BaseModel):
    task_id: str
    provider: Literal["stub", "llama_cpp"] = "stub"
    script: list[str] = []
    # budgets must be at least 1, or Budget raises and the request fails with 500
    max_steps: int = Field(default=8, ge=1)
    max_tokens: int = Field(default=20000, ge=1)
    model: str | None = None
    base_url: str | None = None

class RunOut(BaseModel):
    id: str
    task_id: str
    status: RunStatus
    provider: str
    steps: int
    tokens: int
    result: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class ApprovalDecision(BaseModel):
    approved: bool
    decided_by: str = Field(min_length=1, max_length=80)
    # the stub provider needs a continuation script; a real provider needs its endpoint
    script: list[str] = []
    model: str | None = None
    base_url: str | None = None
