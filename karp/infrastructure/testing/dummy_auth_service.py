from typing import List


from karp.domain import value_objects
from karp.domain.models.user import User
from karp.services import auth_service, context


class DummyAuthService(auth_service.AuthService, auth_service_type="dummy_auth"):
    def __init__(self):
        pass
        # if False or config.TESTING or config.DEBUG:
        #     raise RuntimeError("Don't use this in production!")

    def authenticate(self, scheme: str, credentials: str) -> User:
        return User(credentials or "dummy", {}, {})

    def authorize(
        self,
        level: value_objects.PermissionLevel,
        user: User,
        resource_ids: List[str],
    ) -> bool:
        return True
