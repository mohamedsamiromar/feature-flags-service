"""Query layer for the environment app — the only place with ORM access for
environments and per-environment flag state."""

from apps.core.errors import APIError, Error
from apps.environment.models import Environment, EnvironmentFlag


class EnvironmentQuery:
    @staticmethod
    def get_in_project(pk, project) -> Environment:
        try:
            return Environment.objects.get(pk=pk, project=project)
        except Environment.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Environment"])

    @staticmethod
    def get_in_project_by_name(name: str, project) -> Environment:
        try:
            return Environment.objects.get(project=project, name=name)
        except Environment.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Environment"])

    @staticmethod
    def get_for_member(pk, user) -> Environment:
        """Fetch an environment the caller can see through project membership.

        Used where an environment is referenced by id outside its nested route
        (e.g. SDK-key creation). Not a member → 404."""
        try:
            return (
                Environment.objects
                .select_related("project")
                .get(pk=pk, project__organization__memberships__user=user)
            )
        except Environment.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Environment"])

    @staticmethod
    def list_for_project(project):
        return Environment.objects.filter(project=project).order_by("name")

    @staticmethod
    def create(project, **fields) -> Environment:
        return Environment.objects.create(project=project, **fields)

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
