from rest_framework import status
from rest_framework.exceptions import APIException


class FlagNotFoundError(Exception):
    pass


class InvalidOperatorError(Exception):
    pass


class FlagArchivedError(APIException):
    """Raised when a mutating operation is attempted on an archived flag."""
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Cannot modify an archived flag. Unarchive it first."
    default_code = "flag_archived"
