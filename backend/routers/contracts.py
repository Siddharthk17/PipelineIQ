"""Data contract CRUD API — matches the Week 11 roadmap specification.

POST   /api/contracts            — create a contract for a pipeline
GET    /api/contracts            — list contracts owned by current user
GET    /api/contracts/{id}/breaches — breach history for a contract
DELETE /api/contracts/{id}       — delete a contract
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, delete as sqla_delete
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import (
    ContractViolationRecord,
    PipelineContract,
    PipelineRun,
    User,
)
from backend.utils.uuid_utils import as_uuid, validate_uuid_format

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contracts", tags=["Contracts"])


class CreateContractRequest(BaseModel):
    pipeline_name: str = Field(..., min_length=1, max_length=500)
    output_schema: dict = Field(
        ...,
        description="{column_name: {type, nullable, description}}",
    )
    consumers: list[str] = Field(default_factory=list)
    severity: str = Field(default="warn", pattern="^(warn|block)$")
    owner_email: str | None = None
    null_thresholds: dict = Field(default_factory=dict)
    min_rows: int | None = None
    max_rows: int | None = None


class ContractResponse(BaseModel):
    id: str
    pipeline_name: str
    output_schema: dict
    consumers: list[str]
    severity: str
    owner_email: str | None
    null_thresholds: dict
    min_rows: int | None
    max_rows: int | None
    created_at: str


@router.post("", response_model=ContractResponse, status_code=201)
def create_contract(
    request: CreateContractRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = (
        db.query(PipelineContract)
        .filter(
            PipelineContract.pipeline_name == request.pipeline_name,
            PipelineContract.user_id == str(current_user.id),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            409,
            f"A contract for pipeline '{request.pipeline_name}' already exists. "
            f"Use PUT to update it.",
        )

    contract = PipelineContract(
        pipeline_name=request.pipeline_name,
        yaml_content="",
        output_schema=request.output_schema,
        severity=request.severity,
        consumers=request.consumers,
        user_id=str(current_user.id),
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)

    return ContractResponse(
        id=str(contract.id),
        pipeline_name=contract.pipeline_name,
        output_schema=contract.output_schema or {},
        consumers=contract.consumers or [],
        severity=contract.severity.value
        if hasattr(contract.severity, "value")
        else str(contract.severity),
        owner_email=None,
        null_thresholds={},
        min_rows=None,
        max_rows=None,
        created_at=contract.created_at.isoformat() if contract.created_at else "",
    )


@router.get("", response_model=list[ContractResponse])
def list_contracts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contracts = (
        db.query(PipelineContract)
        .filter(PipelineContract.user_id == str(current_user.id))
        .order_by(PipelineContract.pipeline_name.asc())
        .all()
    )
    return [
        ContractResponse(
            id=str(c.id),
            pipeline_name=c.pipeline_name,
            output_schema=c.output_schema or {},
            consumers=c.consumers or [],
            severity=c.severity.value
            if hasattr(c.severity, "value")
            else str(c.severity),
            owner_email=None,
            null_thresholds={},
            min_rows=None,
            max_rows=None,
            created_at=c.created_at.isoformat() if c.created_at else "",
        )
        for c in contracts
    ]


@router.get("/{contract_id}/breaches")
def list_contract_breaches(
    contract_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    validate_uuid_format(contract_id)
    contract = (
        db.query(PipelineContract)
        .filter(
            PipelineContract.id == as_uuid(contract_id),
            PipelineContract.user_id == str(current_user.id),
        )
        .first()
    )
    if not contract:
        raise HTTPException(404, "Contract not found")

    violations = (
        db.query(ContractViolationRecord)
        .join(PipelineRun, ContractViolationRecord.run_id == PipelineRun.id)
        .filter(
            PipelineRun.name == contract.pipeline_name,
            PipelineRun.user_id == current_user.id,
        )
        .order_by(ContractViolationRecord.created_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "contract_id": contract_id,
        "breaches": [
            {
                "id": str(v.id),
                "run_id": str(v.run_id),
                "breach_type": v.rule,
                "field": v.column,
                "expected": v.expected,
                "actual": v.actual,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in violations
        ],
        "total": len(violations),
    }


@router.delete("/{contract_id}", status_code=204)
def delete_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    validate_uuid_format(contract_id)
    contract = (
        db.query(PipelineContract)
        .filter(
            PipelineContract.id == as_uuid(contract_id),
            PipelineContract.user_id == str(current_user.id),
        )
        .first()
    )
    if not contract:
        raise HTTPException(404, "Contract not found")
    db.delete(contract)
    db.commit()
    return None
