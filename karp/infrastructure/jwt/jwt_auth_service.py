"""Module for jwt-based authentication."""
from karp.services import context
from pathlib import Path
import time
from typing import List

import jwt
import jwt.exceptions as jwte  # pyre-ignore

from karp.domain import errors, value_objects
from karp.domain.models.user import User
from karp.domain.errors import AuthError
from karp.services import auth_service, context, unit_of_work

# from karp.infrastructure.unit_of_work import unit_of_work

# import karp.resourcemgr as resourcemgr
from karp.errors import KarpError, ClientErrorCodes


def load_jwt_key(path: Path) -> str:
    with open(path) as fp:
        return fp.read()


# jwt_key = load_jwt_key(config.JWT_AUTH_PUBKEY_PATH)


class JWTAuthenticator(
    auth_service.AuthService, auth_service_type="jwt_auth", is_default=True
):
    def __init__(
        self, pubkey_path: Path, resource_uow: unit_of_work.ResourceUnitOfWork
    ) -> None:
        self._jwt_key = load_jwt_key(pubkey_path)
        self._resource_uow = resource_uow
        print("JWTAuthenticator created")

    def authenticate(self, _scheme: str, credentials: str) -> User:
        print("JWTAuthenticator.authenticate: called")

        try:
            user_token = jwt.decode(
                credentials, key=self._jwt_key, algorithms=["RS256"]
            )
        except jwte.ExpiredSignatureError as exc:
            raise AuthError(
                "The given jwt have expired", code=ClientErrorCodes.EXPIRED_JWT
            ) from exc
        except jwte.DecodeError as exc:
            raise AuthError("General JWT error") from exc

        lexicon_permissions = {}
        if "scope" in user_token and "lexica" in user_token["scope"]:
            lexicon_permissions = user_token["scope"]["lexica"]
        return User(user_token["sub"], lexicon_permissions, user_token["levels"])

    def authorize(
        self,
        level: value_objects.PermissionLevel,
        user: User,
        resource_ids: List[str],
    ):

        with self._resource_uow as resources_uw:
            for resource_id in resource_ids:
                resource = resources_uw.resources.by_resource_id(resource_id)
                if not resource:
                    raise errors.ResourceNotFound(resource_id=resource_id)
                if resource.is_protected(level) and (
                    not user
                    or not user.permissions.get(resource_id)
                    or user.permissions[resource_id] < user.levels[level]
                ):
                    return False
        return True
