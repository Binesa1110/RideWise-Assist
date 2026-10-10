from dataclasses import dataclass
from typing import Optional


ROLE_PERMISSIONS = {
    "customer": {"booking:read:self", "damage:read:self"},
    "support": {"booking:read:all", "damage:read:all", "payment:read:all"},
    "admin": {
        "booking:read:all",
        "damage:read:all",
        "payment:read:all",
        "policy:read:all",
        "analytics:read:all",
    },
}


@dataclass
class DevelopmentUser:
    user_id: int
    username: str
    role: str


class AccessControl:
    def __init__(self):
        self.users = {
            "customer": DevelopmentUser(1, "customer", "customer"),
            "support": DevelopmentUser(2, "support", "support"),
            "admin": DevelopmentUser(3, "admin", "admin"),
        }

    def login(self, username: str, role: str | None = None) -> Optional[DevelopmentUser]:
        normalized = (username or "").strip().lower()
        if not normalized:
            return None
        if normalized in self.users:
            user = self.users[normalized]
            if role and user.role != role:
                return None
            return user
        if role and role in ROLE_PERMISSIONS:
            return DevelopmentUser(user_id=0, username=username, role=role)
        return None

    def can_access_record(self, actor: DevelopmentUser | None, record_owner_id: int | None, resource: str) -> bool:
        if actor is None:
            return False

        permissions = ROLE_PERMISSIONS.get(actor.role, set())
        if resource == "analytics":
            return "analytics:read:all" in permissions
        if any(
            permission in permissions
            for permission in [
                "booking:read:all",
                "payment:read:all",
                "damage:read:all",
                "analytics:read:all",
            ]
        ):
            return True

        if actor.role == "customer":
            if resource in {"booking", "payment", "damage"}:
                return record_owner_id == actor.user_id
            return True

        return False

    def authorize(self, actor: DevelopmentUser | None, resource: str, record_owner_id: int | None = None) -> bool:
        return self.can_access_record(actor, record_owner_id, resource)

    def resolve_verified_user(self, actor: DevelopmentUser | None) -> DevelopmentUser | None:
        """Resolve only an exact known development account, never role claims."""
        if (
            not isinstance(actor, DevelopmentUser)
            or not isinstance(actor.username, str)
            or not isinstance(actor.role, str)
            or type(actor.user_id) is not int
        ):
            return None
        username = actor.username.strip().lower()
        known = self.users.get(username)
        if (
            known is None
            or actor.user_id != known.user_id
            or actor.role != known.role
        ):
            return None
        return known


DEFAULT_ACCESS_CONTROL = AccessControl()


def get_development_user(username: str, role: str | None = None) -> Optional[DevelopmentUser]:
    return DEFAULT_ACCESS_CONTROL.login(username, role)


def can_access_record(username: str, role: str, resource: str, record_owner_id: int | None = None) -> bool:
    user = DEFAULT_ACCESS_CONTROL.login(username, role)
    return DEFAULT_ACCESS_CONTROL.authorize(user, resource, record_owner_id) if user else False
