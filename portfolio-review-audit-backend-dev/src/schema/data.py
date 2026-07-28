"""Request/response models for data sync (aligned with portfolio-review-app-api-develop)."""

from typing import List, Optional

from pydantic import BaseModel, Field


class DataRequest(BaseModel):
    migration_type: List[str] = Field(default_factory=list)
    manipulation_type: List[str] = Field(default_factory=list)


class ScriptResult(BaseModel):
    executed_scripts: List[str]
    results: dict[str, str]


class DataResponse(BaseModel):
    migration_results: Optional[ScriptResult] = None
    manipulation_results: Optional[ScriptResult] = None
