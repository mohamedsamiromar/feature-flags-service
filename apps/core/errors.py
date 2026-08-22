import logging
from enum import Enum

from django.utils.translation import gettext_lazy as _
from rest_framework import status

logger = logging.getLogger("django")

# Business error codes are negative and stable; the HTTP status is what clients
# branch on, while `code` gives a precise, language-independent handle for a
# specific failure. Keep codes unique. Last used code: -415.


class Error(Enum):
    DEFAULT = {
        "code": -301,
        "http_status": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "detail": _("Oops!. Something went wrong, please contact us"),
    }
    INSTANCE_NOT_FOUND = {
        "code": -404,
        "http_status": status.HTTP_404_NOT_FOUND,
        "detail": _("{} not found"),
    }
    INSTANCE_ID_NOT_FOUND = {
        "code": -404,
        "http_status": status.HTTP_404_NOT_FOUND,
        "detail": _("{} with ID ({}) is not found"),
    }
    REQUIRED_FIELD = {
        "code": -304,
        "http_status": status.HTTP_400_BAD_REQUEST,
        "detail": _("This field is required"),
    }
    ALREADY_IN_STATE = {
        "code": -305,
        "http_status": status.HTTP_409_CONFLICT,
        "detail": _("{} is already {}"),
    }
    FLAG_ARCHIVED = {
        "code": -409,
        "http_status": status.HTTP_409_CONFLICT,
        "detail": _("Cannot modify an archived flag. Unarchive it first."),
    }
    INVALID_OPERATOR = {
        "code": -422,
        "http_status": status.HTTP_400_BAD_REQUEST,
        "detail": _("Unknown operator: ({})"),
    }
    VARIATION_NOT_IN_FLAG = {
        "code": -306,
        "http_status": status.HTTP_400_BAD_REQUEST,
        "detail": _("{} does not belong to this flag."),
    }
    INVALID_ENVIRONMENT = {
        "code": -307,
        "http_status": status.HTTP_400_BAD_REQUEST,
        "detail": _("Environment not found or not owned by you."),
    }
    INVALID_FLAG_REF = {
        "code": -308,
        "http_status": status.HTTP_400_BAD_REQUEST,
        "detail": _("Invalid pk — flag not found or not accessible."),
    }
    INSUFFICIENT_ROLE = {
        "code": -410,
        "http_status": status.HTTP_403_FORBIDDEN,
        "detail": _("Your role does not permit this action."),
    }
    LAST_OWNER = {
        "code": -411,
        "http_status": status.HTTP_409_CONFLICT,
        "detail": _("An organization must keep at least one owner."),
    }
    IMMUTABLE_FIELD = {
        "code": -415,
        "http_status": status.HTTP_400_BAD_REQUEST,
        "detail": _("({}) cannot be changed after creation."),
    }


class APIError(Exception):
    """Single exception the service layer raises for business failures.

    Carries an ``Error`` member whose ``detail`` may contain ``{}`` placeholders
    filled from ``extra``. The global exception handler
    (``config.exception_handler``) renders ``self.error`` with ``http_status``,
    so services never touch HTTP concerns.

    Usage:
        raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Flag"])
        raise APIError(Error.INSTANCE_ID_NOT_FOUND, extra=["Variation", 42])
    """

    def __init__(self, error: Error, extra=None, code: int = None):
        value = error.value
        detail = str(value["detail"])
        self.extra = extra or None
        if isinstance(self.extra, (list, tuple)):
            detail = detail.format(*self.extra)
        elif self.extra is not None:
            detail = detail.format(self.extra)
        self.error = {
            "code": code if code is not None else value["code"],
            "detail": detail,
            "alert": value.get("alert", False),
        }
        self.http_status = value.get("http_status", status.HTTP_400_BAD_REQUEST)
        super().__init__(self.error)
