from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.evaluation.services import FlagEvaluationService
from apps.evaluation.tasks import log_evaluation
from apps.sdk.serializers import (
    SDKConfigResponseSerializer,
    SDKImpressionBatchSerializer,
    SDKImpressionResponseSerializer,
    SDKEvaluateAllRequestSerializer,
    SDKEvaluateAllResponseSerializer,
    SDKEvaluateRequestSerializer,
    SDKEvaluateResponseSerializer,
)
from apps.sdk_keys.authentication import SDKKeyAuthentication
from apps.sdk_keys.permissions import HasSDKKey, HasServerSDKKey

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


class SDKConfigView(APIView):
    """
    GET /api/v1/sdk/flags/config/
    Header: X-SDK-Key: sdk_srv_<token>
    Optional: If-None-Match: "<config_version>"

    The environment's whole ruleset, unevaluated, for a server-side SDK that
    evaluates in-process. Specified in SDK_CONFIG_SPEC.md.

    Not an alternative to `POST /sdk/flags/evaluate/` — the opposite cost
    profile. The bootstrap endpoint costs one round trip per *user context*,
    which is right for a browser (one user per session) and wrong for a server
    SDK calling `variation(flag, user)` for thousands of users inside its own
    request path. This endpoint moves that cost to one round trip per *config
    change*: download once at process start, evaluate locally, re-fetch when
    the version moves.

    **Server keys only.** See `HasServerSDKKey` — the payload contains real
    user identifiers, and client keys ship to browsers.

    **No impression logging.** A config fetch is not an evaluation; nothing has
    been served to anyone yet. Impressions for locally-evaluated flags arrive
    through the batching endpoint, the same reasoning that keeps them off the
    bootstrap endpoint.
    """

    authentication_classes = [SDKKeyAuthentication]
    permission_classes = [HasServerSDKKey]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "config_download"

    # A polling SDK is told how often to ask, so every SDK does not invent its
    # own interval. Advisory: the 304 is what makes the poll cheap, not this.
    POLL_INTERVAL_SECONDS = 30

    def get(self, request):
        sdk_key = request.auth
        environment = sdk_key.environment

        # Read the version once and build the payload against that exact value.
        # Re-reading it for the ETag could pick up a bump that happened after
        # the payload was assembled, and the SDK would then cache stale content
        # under a version it believes is current — and stop asking.
        config_version = environment.config_version
        etag = f'"{config_version}"'

        if self._matches(request.headers.get("If-None-Match"), etag):
            # The common case for a poller: nothing changed, so no body.
            return self._respond(Response(status=status.HTTP_304_NOT_MODIFIED), etag)

        config = _eval_service.config_for(
            project_id=environment.project_id,
            env_id=environment.id,
            environment_name=environment.name,
            config_version=config_version,
        )
        return self._respond(
            Response(SDKConfigResponseSerializer(config).data), etag
        )

    def _respond(self, response: Response, etag: str) -> Response:
        response["ETag"] = etag
        response["Cache-Control"] = f"max-age={self.POLL_INTERVAL_SECONDS}, private"
        return response

    @staticmethod
    def _matches(if_none_match: str, etag: str) -> bool:
        """RFC 9110 If-None-Match: a comma-separated list, or `*`.

        Weak validators (`W/"4127"`) are compared by stripping the prefix —
        this resource has no strong/weak distinction, since the version *is*
        the identity of the content. Parsed rather than string-compared because
        HTTP intermediaries legitimately rewrite and combine these.
        """
        if not if_none_match:
            return False
        if if_none_match.strip() == "*":
            return True
        candidates = {
            candidate.strip()[2:] if candidate.strip().startswith("W/") else candidate.strip()
            for candidate in if_none_match.split(",")
        }
        return etag in candidates


class SDKImpressionsView(APIView):
    """
    POST /api/v1/sdk/impressions/
    Header: X-SDK-Key: sdk_srv_<token> or sdk_cli_<token>

    Body: { "impressions": [
        { "flag_key": "dark-mode", "result": true,
          "user_context": {"user_id": "u_1"} }
    ] }

    Bulk impression ingest, for flags an SDK resolved without asking the server.

    Without it, local evaluation is invisible. An SDK working from
    `GET /sdk/flags/config/` never calls `POST /sdk/evaluate/`, so nothing it
    serves reaches `EvaluationLog` — and the same is true of every flag pulled
    through the bootstrap endpoint, which deliberately logs nothing. This is
    where those impressions arrive.

    Accepts both key types. A browser SDK bootstrapping a session has the same
    problem as a server SDK evaluating locally: it read flags the server has no
    record of serving.

    `202`, not `201`: the rows are written by a Celery worker, so what the
    response confirms is that the batch was queued, not that it is queryable.
    """

    authentication_classes = [SDKKeyAuthentication]
    permission_classes = [HasSDKKey]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "impressions"

    def post(self, request):
        sdk_key = request.auth

        serializer = SDKImpressionBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = _eval_service.record_impressions(
            project_id=sdk_key.environment.project_id,
            env_id=sdk_key.environment_id,
            impressions=serializer.validated_data["impressions"],
        )

        return Response(
            SDKImpressionResponseSerializer(result).data,
            status=status.HTTP_202_ACCEPTED,
        )
