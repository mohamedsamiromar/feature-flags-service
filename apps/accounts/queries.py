"""Query layer for the accounts app — the only place with ORM access to ``User``."""

from apps.accounts.models import User
from apps.core.errors import APIError, Error


class UserQuery:
    @staticmethod
    def create(*, username: str, email: str, password: str) -> User:
        """Create a user with a hashed password.

        ``create_user`` (not ``create``) so the password goes through
        ``set_password``. Writing it raw would store the plaintext in the
        column and silently break authentication.
        """
        return User.objects.create_user(
            username=username, email=email, password=password
        )

    @staticmethod
    def username_exists(username: str) -> bool:
        return User.objects.filter(username=username).exists()

    @staticmethod
    def get_by_username(username: str) -> User:
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["User"])
