# -*- coding: utf-8 -*-
"""Authoritative row-level data scope for every business module.

The important rule is fail closed: authentication decides whether a user may
open a feature, while this module decides which business rows that user may
see.  Business routers should not recreate ownership rules themselves.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from app.auth.models import User
from app.models.dealer_assignment import DealerAssignment
from app.models.dealer_store import DealerStore


VALID_DATA_SCOPES = {"none", "self", "team", "all"}
ROLE_DEFAULT_SCOPES = {
    "viewer": "none",
    "dealer": "self",
    "sales": "self",
    "manager": "team",
    "admin": "all",
}
def normalize_scope_key(value: str | None) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def effective_data_scope(user: User) -> str:
    configured = normalize_scope_key(getattr(user, "data_scope", ""))
    if configured in VALID_DATA_SCOPES:
        # Only admins may receive an unrestricted scope.  This prevents a
        # stale or accidentally edited row from turning a sales account into
        # a global reader.
        if configured == "all" and user.role != "admin":
            return ROLE_DEFAULT_SCOPES.get(user.role, "none")
        # Bound dealers must see their own store; stale "none" is a lockout.
        if (
            configured == "none"
            and user.role == "dealer"
            and str(getattr(user, "dealer_id", "") or "").strip()
        ):
            return ROLE_DEFAULT_SCOPES["dealer"]
        return configured
    return ROLE_DEFAULT_SCOPES.get(user.role, "none")


def effective_owner_key(user: User) -> str:
    # This value must be configured from an immutable business identity.  A
    # display name, email local-part or other fuzzy alias is never sufficient
    # to grant access.
    return str(getattr(user, "owner_key", "") or "").strip()


def effective_team_key(user: User) -> str:
    configured = str(getattr(user, "team_key", "") or "").strip()
    # Never infer a manager's team. Authentication grants feature access, but
    # only an explicit assignment grants access to business rows.
    return configured


def owner_aliases(user: User) -> list[str]:
    """Return explicit source mappings, never inferred display-name aliases."""
    values = [
        effective_owner_key(user),
        getattr(user, "sales_name", "") or "",
    ]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        key = normalize_scope_key(raw)
        if key and key not in seen:
            seen.add(key)
            result.append(raw)
    return result


@dataclass(frozen=True)
class DataScope:
    mode: str
    store_ids: tuple[str, ...]
    dealer_names: tuple[str, ...]
    owner_keys: tuple[str, ...]
    team_key: str = ""

    @property
    def unrestricted(self) -> bool:
        return self.mode == "all"

    def as_session_user_fields(self) -> dict:
        return {
            "data_scope": self.mode,
            "allowed_store_ids": list(self.store_ids),
            "allowed_dealer_names": list(self.dealer_names),
            "scope_owner_keys": list(self.owner_keys),
            "team_key": self.team_key,
        }


def resolve_data_scope(user: User, session: Session) -> DataScope:
    mode = effective_data_scope(user)
    if mode == "all":
        return DataScope(mode="all", store_ids=(), dealer_names=(), owner_keys=())
    if mode == "none":
        return DataScope(mode="none", store_ids=(), dealer_names=(), owner_keys=())

    stmt = select(DealerStore).where(DealerStore.is_active == True)  # noqa: E712
    stores = list(session.exec(stmt).all())

    if user.role == "dealer":
        allowed_id = str(user.dealer_id or "").strip()
        stores = [row for row in stores if allowed_id and row.store_id == allowed_id]
        owner_keys: list[str] = []
        team_key = stores[0].team_key if stores else ""
    elif mode == "self":
        assigned_ids = set()
        if user.id is not None:
            assigned_ids = set(session.exec(
                select(DealerAssignment.store_id).where(
                    DealerAssignment.user_id == user.id,
                    DealerAssignment.is_active == True,  # noqa: E712
                )
            ).all())
        stores = [row for row in stores if row.store_id in assigned_ids]
        owner_keys = [row.sales_owner for row in stores if row.sales_owner]
        # sales_name is an explicit source adapter configured by an admin.  It
        # is useful for customer/logistics records that do not carry store_id,
        # but never grants a store assignment by itself.
        if getattr(user, "sales_name", ""):
            owner_keys.append(user.sales_name)
        team_key = effective_team_key(user)
    elif mode == "team":
        team_key = effective_team_key(user)
        normalized_team = normalize_scope_key(team_key)
        stores = [
            row for row in stores
            if normalized_team and normalize_scope_key(row.team_key) == normalized_team
        ]
        # Owner-scoped modules (customers, logistics, meetings) need the same
        # team boundary even when they do not contain a store_id.
        owner_keys = sorted({row.sales_owner for row in stores if row.sales_owner})
        team_users = session.exec(select(User).where(User.is_active == True)).all() if team_key else []  # noqa: E712
        for member in team_users:
            if member.role == "sales" and normalize_scope_key(member.team_key) == normalized_team:
                owner_keys.extend(owner_aliases(member))
    else:
        return DataScope(mode="none", store_ids=(), dealer_names=(), owner_keys=())

    deduped_owners: list[str] = []
    seen_owners: set[str] = set()
    for value in owner_keys:
        key = normalize_scope_key(value)
        if key and key not in seen_owners:
            seen_owners.add(key)
            deduped_owners.append(value)
    return DataScope(
        mode=mode,
        store_ids=tuple(row.store_id for row in stores),
        dealer_names=tuple(row.name for row in stores),
        owner_keys=tuple(deduped_owners),
        team_key=team_key,
    )


def visible_store_ids(user: User, session: Session) -> list[str] | None:
    scope = resolve_data_scope(user, session)
    return None if scope.unrestricted else list(scope.store_ids)


def visible_dealer_names(user: User, session: Session) -> list[str] | None:
    scope = resolve_data_scope(user, session)
    return None if scope.unrestricted else list(scope.dealer_names)


def visible_owner_keys(user: User, session: Session) -> list[str] | None:
    scope = resolve_data_scope(user, session)
    return None if scope.unrestricted else list(scope.owner_keys)


def filter_rows_by_scope(
    rows: list[dict],
    *,
    owner_keys: list[str] | tuple[str, ...] | None = None,
    dealer_names: list[str] | tuple[str, ...] | None = None,
    owner_field: str = "owner",
    dealer_field: str = "dealer_name",
) -> list[dict]:
    """共享的 dict 行级 scope 过滤（评审整改：收敛六处重复实现）。

    fail-closed：两个集合都为空时返回空列表；归一化（casefold）匹配。
    支持嵌套 owner 的备用键（owner_key/salesperson）。
    """
    owners = {
        normalize_scope_key(value)
        for value in (owner_keys or [])
        if normalize_scope_key(value)
    }
    dealers = {
        normalize_scope_key(value)
        for value in (dealer_names or [])
        if normalize_scope_key(value)
    }
    if not owners and not dealers:
        return []
    result = []
    for row in rows:
        owner_values = [
            row.get(owner_field),
            row.get("owner_key"),
            row.get("salesperson"),
        ]
        dealer_values = [row.get(dealer_field), row.get("customer")]
        if any(
            normalize_scope_key(value) in owners
            for value in owner_values
            if normalize_scope_key(value)
        ):
            result.append(row)
            continue
        if any(
            normalize_scope_key(value) in dealers
            for value in dealer_values
            if normalize_scope_key(value)
        ):
            result.append(row)
    return result


def scoped_active_store_ids(user: User, session: Session) -> list[str]:
    """Return active stores inside the user's scope, including for admins."""
    visible = visible_store_ids(user, session)
    if visible is not None:
        return visible
    return list(session.exec(
        select(DealerStore.store_id).where(DealerStore.is_active == True)  # noqa: E712
    ).all())


def scoped_active_dealer_names(user: User, session: Session) -> list[str]:
    """Return active dealer names inside the user's scope, including admins."""
    visible = visible_store_ids(user, session)
    stmt = select(DealerStore.name).where(DealerStore.is_active == True)  # noqa: E712
    if visible is not None:
        if not visible:
            return []
        stmt = stmt.where(DealerStore.store_id.in_(visible))
    return list(session.exec(stmt).all())


def sync_user_dealer_assignments(user: User, session: Session) -> list[str]:
    """Rebuild a sales user's assignments from an admin-configured owner_key."""
    if user.id is None:
        return []
    existing = session.exec(
        select(DealerAssignment).where(DealerAssignment.user_id == user.id)
    ).all()
    for row in existing:
        session.delete(row)
    if user.role != "sales" or not effective_owner_key(user):
        session.flush()
        return []
    owner_key = normalize_scope_key(effective_owner_key(user))
    stores = session.exec(
        select(DealerStore).where(DealerStore.is_active == True)  # noqa: E712
    ).all()
    assigned = [row.store_id for row in stores if normalize_scope_key(row.sales_owner) == owner_key]
    for store_id in assigned:
        session.add(DealerAssignment(user_id=user.id, store_id=store_id))
    session.flush()
    return assigned


def rebuild_all_dealer_assignments(session: Session) -> int:
    total = 0
    users = session.exec(select(User).where(User.is_active == True)).all()  # noqa: E712
    for user in users:
        if user.role == "sales" and effective_owner_key(user):
            total += len(sync_user_dealer_assignments(user, session))
    session.commit()
    return total
