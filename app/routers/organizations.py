"""
/v1/organizations — Multi-tenant workspace & team management.

A user creates a workspace (becoming its owner), then admins invite teammates and
assign roles (owner/admin/analyst/viewer). Role checks are enforced via
:mod:`app.rbac`. Scenarios and org-private projects are shared across all members.
"""
from __future__ import annotations

import re
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import User, Organization, OrgRole
from app.rbac import require_org_member, require_admin

router = APIRouter()


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100] or "org"
    return f"{base}-{secrets.token_hex(3)}"


# ── Schemas ───────────────────────────────────────────────────────────────────
class OrgCreate(BaseModel):
    name: str = Field(..., max_length=255)
    billing_email: Optional[EmailStr] = None


class MemberAdd(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.viewer


class RoleUpdate(BaseModel):
    role: OrgRole


def _member_out(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "role": u.role.value if u.role else "viewer",
        "company": u.company,
        "job_title": u.job_title,
        "is_active": u.is_active,
    }


def _org_out(org: Organization, member_count: int) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "slug": org.slug,
        "plan": org.plan.value if org.plan else "free",
        "seats": org.seats,
        "seats_used": member_count,
        "billing_email": org.billing_email,
        "created_at": org.created_at,
    }


async def _member_count(db: AsyncSession, org_id) -> int:
    return (await db.execute(
        select(func.count()).select_from(User).where(User.organization_id == org_id)
    )).scalar() or 0


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.post("/", status_code=201, summary="Create a workspace (caller becomes owner)")
async def create_organization(
    payload: OrgCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.organization_id:
        raise HTTPException(status_code=409, detail="You already belong to an organization")
    org = Organization(
        name=payload.name,
        slug=_slugify(payload.name),
        billing_email=payload.billing_email or current_user.email,
        plan=current_user.plan,
    )
    db.add(org)
    await db.flush()  # assign org.id
    current_user.organization_id = org.id
    current_user.role = OrgRole.owner
    await db.commit()
    await db.refresh(org)
    return _org_out(org, 1)


@router.get("/me", summary="Current user's organization + members")
async def my_organization(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_org_member),
):
    org = (await db.execute(
        select(Organization).where(Organization.id == current_user.organization_id)
    )).scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    members = (await db.execute(
        select(User).where(User.organization_id == org.id).order_by(User.created_at)
    )).scalars().all()
    return {"organization": _org_out(org, len(members)),
            "members": [_member_out(m) for m in members],
            "your_role": current_user.role.value}


@router.get("/members", summary="List organization members")
async def list_members(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_org_member),
):
    members = (await db.execute(
        select(User).where(User.organization_id == current_user.organization_id)
    )).scalars().all()
    return {"data": [_member_out(m) for m in members], "count": len(members)}


@router.post("/members", status_code=201, summary="Add a teammate by email (Admin+)")
async def add_member(
    payload: MemberAdd,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if payload.role == OrgRole.owner:
        raise HTTPException(status_code=422, detail="Assign 'owner' via role transfer, not invite")
    target = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404,
                            detail="No registered user with that email. Ask them to sign up first.")
    if target.organization_id and target.organization_id != current_user.organization_id:
        raise HTTPException(status_code=409, detail="User already belongs to another organization")

    org = (await db.execute(
        select(Organization).where(Organization.id == current_user.organization_id)
    )).scalar_one()
    used = await _member_count(db, org.id)
    if target.organization_id != org.id and org.seats and used >= org.seats:
        raise HTTPException(status_code=402,
                            detail=f"Seat limit reached ({org.seats}). Upgrade to add members.")
    target.organization_id = org.id
    target.role = payload.role
    await db.commit()
    return _member_out(target)


@router.patch("/members/{user_id}", summary="Change a member's role (Admin+)")
async def update_member_role(
    user_id: str,
    payload: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    target = (await db.execute(
        select(User).where(User.id == user_id,
                           User.organization_id == current_user.organization_id)
    )).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Member not found in your organization")
    # Don't allow removing the last owner via demotion
    if target.role == OrgRole.owner and payload.role != OrgRole.owner:
        owners = (await db.execute(
            select(func.count()).select_from(User).where(
                User.organization_id == current_user.organization_id, User.role == OrgRole.owner)
        )).scalar()
        if owners <= 1:
            raise HTTPException(status_code=409, detail="Cannot demote the last owner")
    target.role = payload.role
    await db.commit()
    return _member_out(target)


@router.delete("/members/{user_id}", status_code=204, summary="Remove a member (Admin+)")
async def remove_member(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if str(current_user.id) == str(user_id):
        raise HTTPException(status_code=409, detail="Use role transfer; you cannot remove yourself")
    target = (await db.execute(
        select(User).where(User.id == user_id,
                           User.organization_id == current_user.organization_id)
    )).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Member not found in your organization")
    if target.role == OrgRole.owner:
        raise HTTPException(status_code=409, detail="Transfer ownership before removing an owner")
    target.organization_id = None
    target.role = OrgRole.viewer
    await db.commit()
