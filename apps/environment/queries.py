"""Query layer for the environment app — the only place with ORM access for
environments and per-environment flag state."""

from apps.core.errors import APIError, Error
from apps.environment.models import Environment, EnvironmentFlag


class EnvironmentQuery:
    @staticmethod
    def get_owned(pk, user) -> Environment:
        try:
            return Environment.objects.get(pk=pk, owner=user)
        except Environment.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Environment"])

    @staticmethod
    def get_owned_by_name(name: str, user) -> Environment:
        try:
            return Environment.objects.get(owner=user, name=name)
        except Environment.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Environment"])

    @staticmethod
    def list_for_owner(user):
        return Environment.objects.filter(owner=user).order_by("name")

    @staticmethod
    def exists_for_owner(pk, user) -> bool:
        return Environment.objects.filter(pk=pk, owner=user).exists()

    @staticmethod
    def create(owner, **fields) -> Environment:
        return Environment.objects.create(owner=owner, **fields)

    @staticmethod
    def delete(environment: Environment) -> None:
        environment.delete()


class EnvironmentFlagQuery:
    @staticmethod
    def get_or_create(flag, environment):
        env_flag, _ = EnvironmentFlag.objects.get_or_create(
            feature_flag=flag, environment=environment
        )
        return env_flag

    @staticmethod
    def get_for_env(environment, flag_id) -> EnvironmentFlag:
        try:
            return (
                EnvironmentFlag.objects
                .select_related("feature_flag")
                .get(pk=flag_id, environment=environment)
            )
        except EnvironmentFlag.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Environment flag"])

    @staticmethod
    def list_for_env(environment):
        return (
            EnvironmentFlag.objects
            .filter(environment=environment)
            .select_related("feature_flag")
        )

    @staticmethod
    def save(env_flag: EnvironmentFlag, update_fields=None) -> EnvironmentFlag:
        env_flag.save(update_fields=update_fields)
        return env_flag
