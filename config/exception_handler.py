from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.errors import APIError


def api_exception_handler(exc, context):
    """DRF exception handler that also renders service-layer ``APIError``s.

    DRF's default handler runs first, so framework concerns (validation errors,
    ``Http404``, auth, throttling, ``APIException``) keep their existing
    behavior. An unrecognized ``APIError`` is rendered with its declared
    ``http_status`` and ``{code, detail, alert}`` body — this is what lets views
    stay free of ``try/except`` for business errors.
    """
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    if isinstance(exc, APIError):
        return Response(exc.error, status=exc.http_status)

    return None
