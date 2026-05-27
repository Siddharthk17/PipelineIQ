"""Data contract API endpoints.

Provides CRUD operations for pipeline data contract definitions plus
read-only access to contract violations detected during pipeline execution.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import (
    ContractViolationRecord,
    PipelineContract,
    PipelineRun,
    User,
)
from backend.schemas import (
    ContractCreateRequest,
    ContractDefResponse,
    ContractListResponse,
    ContractStatusResponse,
    ContractUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["contracts"])


# ── Contract definition CRUD ────────────────────────────────────────────────


@router.get("/pipelines/{pipeline_name}", response_model=ContractListResponse)
def list_contracts(
    pipeline_name: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """List all contract definitions for a pipeline."""
    contracts = (
        db.query(PipelineContract)
        .filter(PipelineContract.pipeline_name == pipeline_name)
        .order_by(PipelineContract.version.desc())
        .all()
    )
    return ContractListResponse(
        pipeline_name=pipeline_name,
        contracts=[
            ContractDefResponse(
                id=str(c.id),
                pipeline_name=c.pipeline_name,
                version=c.version,
                yaml_content=c.yaml_content,
                severity=c.severity.value if hasattr(c.severity, "value") else str(c.severity),
                consumers=c.consumers or [],
                is_active=c.is_active,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in contracts
        ],
        total=len(contracts),
    )


@router.post(
    "/pipelines/{pipeline_name}",
    response_model=ContractDefResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_contract(
    pipeline_name: str,
    body: ContractCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new data contract definition for a pipeline."""
    latest = (
        db.query(PipelineContract)
        .filter(PipelineContract.pipeline_name == pipeline_name)
        .order_by(PipelineContract.version.desc())
        .first()
    )
    next_version = (latest.version + 1) if latest else 1

    contract = PipelineContract(
        pipeline_name=pipeline_name,
        version=next_version,
        yaml_content=body.yaml_content,
        severity=body.severity,
        consumers=body.consumers,
        user_id=str(current_user.id),
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)

    return ContractDefResponse(
        id=str(contract.id),
        pipeline_name=contract.pipeline_name,
        version=contract.version,
        yaml_content=contract.yaml_content,
        severity=contract.severity.value if hasattr(contract.severity, "value") else str(contract.severity),
        consumers=contract.consumers or [],
        is_active=contract.is_active,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
    )


@router.get(
    "/pipelines/{pipeline_name}/{contract_id}",
    response_model=ContractDefResponse,
)
def get_contract(
    pipeline_name: str,
    contract_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """Get a specific contract definition by ID."""
    contract = (
        db.query(PipelineContract)
        .filter(
            PipelineContract.id == contract_id,
            PipelineContract.pipeline_name == pipeline_name,
        )
        .first()
    )
    if not contract:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract {contract_id} not found for pipeline {pipeline_name}",
        )
    return ContractDefResponse(
        id=str(contract.id),
        pipeline_name=contract.pipeline_name,
        version=contract.version,
        yaml_content=contract.yaml_content,
        severity=contract.severity.value if hasattr(contract.severity, "value") else str(contract.severity),
        consumers=contract.consumers or [],
        is_active=contract.is_active,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
    )


@router.put(
    "/pipelines/{pipeline_name}/{contract_id}",
    response_model=ContractDefResponse,
)
def update_contract(
    pipeline_name: str,
    contract_id: str,
    body: ContractUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update an existing contract definition (creates a new version)."""
    existing = (
        db.query(PipelineContract)
        .filter(
            PipelineContract.id == contract_id,
            PipelineContract.pipeline_name == pipeline_name,
        )
        .first()
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract {contract_id} not found for pipeline {pipeline_name}",
        )

    # Deactivate the existing version, create a new one
    existing.is_active = False

    next_version = existing.version + 1
    new_contract = PipelineContract(
        pipeline_name=pipeline_name,
        version=next_version,
        yaml_content=body.yaml_content,
        severity=body.severity,
        consumers=body.consumers,
        user_id=str(current_user.id),
        is_active=True,
    )
    db.add(new_contract)
    db.commit()
    db.refresh(new_contract)

    return ContractDefResponse(
        id=str(new_contract.id),
        pipeline_name=new_contract.pipeline_name,
        version=new_contract.version,
        yaml_content=new_contract.yaml_content,
        severity=new_contract.severity.value if hasattr(new_contract.severity, "value") else str(new_contract.severity),
        consumers=new_contract.consumers or [],
        is_active=new_contract.is_active,
        created_at=new_contract.created_at,
        updated_at=new_contract.updated_at,
    )


@router.delete(
    "/pipelines/{pipeline_name}/{contract_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_contract(
    pipeline_name: str,
    contract_id: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """Delete a contract definition."""
    contract = (
        db.query(PipelineContract)
        .filter(
            PipelineContract.id == contract_id,
            PipelineContract.pipeline_name == pipeline_name,
        )
        .first()
    )
    if not contract:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract {contract_id} not found for pipeline {pipeline_name}",
        )
    db.delete(contract)
    db.commit()
    return None


@router.get(
    "/pipelines/{pipeline_name}/status",
    response_model=ContractStatusResponse,
)
def get_contract_status(
    pipeline_name: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """Get the status of a pipeline's active contract against its latest run."""
    active = (
        db.query(PipelineContract)
        .filter(
            PipelineContract.pipeline_name == pipeline_name,
            PipelineContract.is_active.is_(True),
        )
        .first()
    )

    last_run = (
        db.query(PipelineRun)
        .filter(PipelineRun.name == pipeline_name)
        .order_by(PipelineRun.created_at.desc())
        .first()
    )

    total_violations = 0
    if active and last_run:
        total_violations = (
            db.query(ContractViolationRecord)
            .filter(ContractViolationRecord.run_id == str(last_run.id))
            .count()
        )

    return ContractStatusResponse(
        pipeline_name=pipeline_name,
        has_contract=active is not None,
        active_contract_id=str(active.id) if active else None,
        active_contract_version=active.version if active else None,
        last_run_id=str(last_run.id) if last_run else None,
        last_run_status=last_run.status.value if last_run else None,
        total_violations=total_violations,
    )


@router.get("/pipelines/{pipeline_name}/breaches")
def list_pipeline_breaches(
    pipeline_name: str,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """List contract violations for the latest run of a pipeline."""
    last_run = (
        db.query(PipelineRun)
        .filter(PipelineRun.name == pipeline_name)
        .order_by(PipelineRun.created_at.desc())
        .first()
    )
    if not last_run:
        return {"run_id": None, "total_violations": 0, "violations": []}

    violations = (
        db.query(ContractViolationRecord)
        .filter(ContractViolationRecord.run_id == str(last_run.id))
        .order_by(
            ContractViolationRecord.step_index,
            ContractViolationRecord.severity.desc(),
        )
        .all()
    )

    return {
        "run_id": str(last_run.id),
        "total_violations": len(violations),
        "violations": [
            {
                "id": str(v.id),
                "step_name": v.step_name,
                "step_index": v.step_index,
                "step_type": v.step_type,
                "column": v.column,
                "rule": v.rule,
                "severity": v.severity,
                "message": v.message,
                "actual": v.actual,
                "expected": v.expected,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in violations
        ],
    }


# ── Contract violation read endpoints ───────────────────────────────────────


@router.get("/runs/{run_id}")
def list_run_violations(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all contract violations for a given pipeline run."""
    run = db.query(PipelineRun).filter(
        PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run {run_id} not found",
        )

    violations = (
        db.query(ContractViolationRecord)
        .filter(ContractViolationRecord.run_id == run_id)
        .order_by(
            ContractViolationRecord.step_index,
            ContractViolationRecord.severity.desc(),
        )
        .all()
    )

    return {
        "run_id": run_id,
        "total_violations": len(violations),
        "violations": [
            {
                "id": str(v.id),
                "step_name": v.step_name,
                "step_index": v.step_index,
                "step_type": v.step_type,
                "column": v.column,
                "rule": v.rule,
                "severity": v.severity,
                "message": v.message,
                "actual": v.actual,
                "expected": v.expected,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in violations
        ],
    }


@router.get("/runs/{run_id}/steps/{step_name}")
def list_step_violations(
    run_id: str,
    step_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List contract violations for a specific step within a run."""
    violations = (
        db.query(ContractViolationRecord)
        .filter(
            ContractViolationRecord.run_id == run_id,
            ContractViolationRecord.step_name == step_name,
        )
        .order_by(ContractViolationRecord.severity.desc())
        .all()
    )

    return {
        "run_id": run_id,
        "step_name": step_name,
        "total_violations": len(violations),
        "violations": [
            {
                "id": str(v.id),
                "column": v.column,
                "rule": v.rule,
                "severity": v.severity,
                "message": v.message,
                "actual": v.actual,
                "expected": v.expected,
            }
            for v in violations
        ],
    }
