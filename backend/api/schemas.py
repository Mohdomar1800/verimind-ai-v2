from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IngestArtifactsRequest(BaseModel):
    mode: str = Field(default="all", pattern="^(metadata|clone|all)$")
    max_commits: int = 250
    max_issues: int = 250
    repos: list[str] | None = None
    skip_clone_if_exists: bool = True


class PipelineRunResponse(BaseModel):
    ok: bool
    exit_code: int
    command: list[str]
    stdout_tail: str
    stderr_tail: str


class ContextItem(BaseModel):
    repo: str
    module_name: str
    context_score: float
    risk_level: str
    tests_total: int = 0
    failures_total: int = 0
    failure_rate: float = 0.0
    coverage_gap: float | None = None
    evidence: dict[str, Any]


class ContextResponse(BaseModel):
    run_id: str
    count: int
    items: list[ContextItem]


class PredictionRequest(BaseModel):
    model_run_id: str | None = None
    run_id: str | None = None
    top_k: int = 20


class RankingItem(BaseModel):
    item_id: str
    score: float
    rank: int
    evidence: dict[str, Any]


class RankingResponse(BaseModel):
    run_id: str
    analysis_type: str
    count: int
    items: list[RankingItem]


class VerificationPlanRequest(BaseModel):
    run_id: str
    top_k_each: int = 10


class VerificationPlanResponse(BaseModel):
    run_id: str
    summary: dict[str, Any]
    plan_items: list[dict[str, Any]]


class ExplanationResponse(BaseModel):
    item_id: str
    run_id: str
    analysis_type: str
    score: float
    rank: int
    evidence: dict[str, Any]
    reasons: list[str]


class UploadLogsResponse(BaseModel):
    ok: bool
    uploaded_files: int
    artifact_rows_upserted: int
    repo: str
    db_path: str | None = None
    context_run_id: str | None = None
    feature_run_id: str | None = None
    parser_summary: dict[str, int] = Field(default_factory=dict)
    pipeline_steps: list[dict[str, Any]] = Field(default_factory=list)
