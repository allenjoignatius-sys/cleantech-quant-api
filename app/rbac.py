"""
Role-Based Access Control (RBAC) for multi-tenant workspaces.

Roles form a strict hierarchy: viewer < analyst < admin < owner. A platform
super-admin (``User.is_admin``) bypasses org-role checks. The core comparison is
a pure function (unit-tested) and the FastAPI dependencies are thin wrappers.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException

from app.auth import get_current_user
from app.models import User, OrgRole

# Strict privilege ordering.
ROLE_LEVEL = {
    OrgRole.viewer: 0,
    OrgRole.analyst: 1,
    OrgRole.admin: 2,
    OrgRole.owner: 3,
}


def role_at_least(user_role: Optional[OrgRole], required: OrgRole) -> bool:
    """True if `user_role` is at least as privileged as `required`."""
    if user_role is None:
        return False
    if not isinstance(user_role, OrgRole):
        user_role = OrgRole(user_role)
    return ROLE_LEVEL.get(user_role, -1) >= ROLE_LEVEL[required]


def require_role(min_role: OrgRole):
    """
    FastAPI dependency factory enforcing a minimum org role.

    Platform super-admins (``is_admin``) always pass. Users without an
    organization fail unless they're a super-admin.
    """
    async def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.is_admin:
            return current_user
        if current_user.organization_id is None:
            raise HTTPException(
                status_code=403,
                detail="You must belong to an organization to perform this action. "
                       "Create or join a workspace first.",
            )
        if not role_at_least(current_user.role, min_role):
            current = current_user.role.value if current_user.role else "none"
            raise HTTPException(
                status_code=403,
                detail=f"Requires '{min_role.value}' role or higher (you are '{current}').",
            )
        return current_user

    return _checker


async def require_org_member(current_user: User = Depends(get_current_user)) -> User:
    """Ensure the caller belongs to an organization (any role)."""
    if current_user.organization_id is None and not current_user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of any organization.",
        )
    return current_user


# Convenience dependencies
require_admin = require_role(OrgRole.admin)
require_analyst = require_role(OrgRole.analyst)
