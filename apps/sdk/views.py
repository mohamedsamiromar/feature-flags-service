from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.evaluation.services import FlagEvaluationService
from apps.evaluation.tasks import log_evaluation
from apps.sdk.serializers import (
    SDKEvaluateAllRequestSerializer,
    SDKEvaluateAllResponseSerializer,
    SDKEvaluateRequestSerializer,
    SDKEvaluateResponseSerializer,
)
from apps.sdk_keys.authentication import SDKKeyAuthentication
from apps.sdk_keys.permissions import HasSDKKey

_eval_service = FlagEvaluationService()


class SDKEvaluateFlagView(APIView):
    """
    POST /api/v1/sdk/evaluate/
    Header: X-SDK-Key: sdk_srv_<token>

    Body: { "flag_key": "dark-mode", "user_context": {"user_id": "u123"} }

    SDK-key-only evaluation endpoint. env_id is derived from the key itself,
    so callers never need to pass it. Both server and client keys are accepted.
    """

    authentication_classes = [SDKKeyAuthentication]
    permission_classes = [HasSDKKey]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "evaluation"

    def post(self, request):
        # SDKKeyAuthentication guarantees request.auth is an SDKKey instance
        # for every authenticated request on this endpoint.
        sdk_key = request.auth

        serializer = SDKEvaluateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        flag_key = serializer.validated_data["flag_key"]
        user_context = serializer.validated_data["user_context"]

        evaluation = _eval_service.evaluate(
            flag_key=flag_key,
            project_id=sdk_key.environment.project_id,
            user_context=user_context,
            env_id=sdk_key.environment_id,
        )

        log_evaluation.delay(
            flag_id=evaluation.flag_id,
            # No user behind an SDK request — the key is the principal.
            user_id=None,
            result=evaluation.result,
            context_data=user_context,
        )

        return Response(
            SDKEvaluateResponseSerializer({
                "flag_key": evaluation.flag_key,
                "result": evaluation.result,
                "result_type": evaluation.result_type,
                "environment": sdk_key.environment.name,
            }).data
        )


class SDKEvaluateAllFlagsView(APIView):
    """
    POST /api/v1/sdk/flags/evaluate/
    Header: X-SDK-Key: sdk_srv_<token>

    Body: { "user_context": {"user_id": "u123", "plan": "pro"} }

    Client bootstrap: every flag configured in the key's environment, resolved
    for one user context in a single call. This is what a browser SDK asks for
    when it starts a session, instead of one request per flag.

    Costs one round trip per *user context*, which is the right shape for one
    user per session and the wrong shape for a server-side SDK evaluating
    thousands of users in-process. That case is served by the config download
    (`GET /sdk/flags/config/`, see SDK_CONFIG_SPEC.md), not by this endpoint.

    POST rather than GET because the user context is an arbitrary nested
    object: query-string encoding it is lossy for anything but flat strings,
    and it would put user attributes into access logs and proxy caches.

    Pure read — no side effects at all, impression logging included. See the
    comment on the response below.
    """

    authentication_classes = [SDKKeyAuthentication]
    permission_classes = [HasSDKKey]
    throttle_classes = [ScopedRateThrottle]
    # Its own scope: one bulk call does the work of N single evaluations, so it
    # must not share the per-flag endpoint's budget.
    throttle_scope = "evaluation_bulk"

    def post(self, request):
        sdk_key = request.auth

        serializer = SDKEvaluateAllRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_context = serializer.validated_data["user_context"]

        evaluations = _eval_service.evaluate_all(
            project_id=sdk_key.environment.project_id,
            env_id=sdk_key.environment_id,
            user_context=user_context,
        )

        # Deliberately no impression logging here. A bootstrap resolves every
        # flag in the environment, but the app may go on to read three of
        # fifty — writing all fifty as impressions inflates `EvaluationLog`
        # (which has no rollup) by an order of magnitude with rows that record
        # a download, not a read. Impressions for these flags arrive through
        # the batching endpoint, where the SDK reports what it actually used.

        return Response(
            SDKEvaluateAllResponseSerializer({
                "environment": sdk_key.environment.name,
                "flags": {
                    evaluation.flag_key: {
                        "result": evaluation.result,
                        "result_type": evaluation.result_type,
                        "variation_id": evaluation.variation_id,
                    }
                    for evaluation in evaluations
                },
            }).data
        )
