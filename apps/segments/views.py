from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.segments.serializers import (
    SegmentRuleSerializer,
    SegmentSerializer,
    SegmentTargetSerializer,
)
from apps.segments.services import SegmentService

_service = SegmentService()


class SegmentViewSet(viewsets.ViewSet):
    """
    Reusable user segments, scoped to a project:

    GET|POST      /projects/{project_key}/segments/
    GET|PATCH|DELETE  /projects/{project_key}/segments/{key}/
    GET|PUT       /projects/{project_key}/segments/{key}/targets/
    DELETE        /projects/{project_key}/segments/{key}/targets/{user_key}/
    GET|POST      /projects/{project_key}/segments/{key}/rules/
    PATCH|DELETE  /projects/{project_key}/segments/{key}/rules/{rule_id}/

    Reads need project membership; writes need MEMBER+ (enforced in the service).
    """

    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "key"
    lookup_value_regex = r"[^/]+"

    @property
    def project_key(self):
        return self.kwargs["project_key"]

    # ------------------------------------------------------------------
    # Segment CRUD
    # ------------------------------------------------------------------

    def list(self, request, **kwargs):
        qs = _service.list_segments(project_key=self.project_key, user=request.user)
        return Response(SegmentSerializer(qs, many=True).data)

    def create(self, request, **kwargs):
        serializer = SegmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        segment = _service.create_segment(
            project_key=self.project_key, user=request.user, **serializer.validated_data
        )
        return Response(SegmentSerializer(segment).data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, key=None, **kwargs):
        segment = _service.get_segment(
            project_key=self.project_key, user=request.user, key=key
        )
        return Response(SegmentSerializer(segment).data)

    def partial_update(self, request, key=None, **kwargs):
        serializer = SegmentSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        segment = _service.update_segment(
            project_key=self.project_key, segment_key=key, user=request.user,
            **serializer.validated_data,
        )
        return Response(SegmentSerializer(segment).data)

    def destroy(self, request, key=None, **kwargs):
        _service.delete_segment(
            project_key=self.project_key, key=key, user=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Individually named members
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get", "put"], url_path="targets")
    def targets(self, request, key=None, **kwargs):
        """List named members, or put one explicitly in/out of the segment.

        PUT is an idempotent upsert: 201 the first time, 200 when moving a user
        between the include and exclude list.
        """
        if request.method == "GET":
            qs = _service.list_targets(
                project_key=self.project_key, key=key, user=request.user
            )
            return Response(SegmentTargetSerializer(qs, many=True).data)

        serializer = SegmentTargetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target, created = _service.set_target(
            project_key=self.project_key, key=key, user=request.user,
            user_key=serializer.validated_data["user_key"],
            excluded=serializer.validated_data.get("excluded", False),
        )
        return Response(
            SegmentTargetSerializer(target).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["delete"], url_path=r"targets/(?P<user_key>[^/]+)")
    def target_detail(self, request, key=None, user_key=None, **kwargs):
        _service.remove_target(
            project_key=self.project_key, key=key, user=request.user, user_key=user_key
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Attribute rules
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get", "post"], url_path="rules")
    def rules(self, request, key=None, **kwargs):
        if request.method == "GET":
            qs = _service.list_rules(
                project_key=self.project_key, key=key, user=request.user
            )
            return Response(SegmentRuleSerializer(qs, many=True).data)

        serializer = SegmentRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = _service.create_rule(
            project_key=self.project_key, key=key, user=request.user,
            **serializer.validated_data,
        )
        return Response(SegmentRuleSerializer(rule).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch", "delete"], url_path=r"rules/(?P<rule_id>[^/.]+)")
    def rule_detail(self, request, key=None, rule_id=None, **kwargs):
        if request.method == "DELETE":
            _service.delete_rule(
                project_key=self.project_key, key=key, user=request.user, rule_id=rule_id
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = SegmentRuleSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        rule = _service.update_rule(
            project_key=self.project_key, key=key, user=request.user,
            rule_id=rule_id, **serializer.validated_data,
        )
        return Response(SegmentRuleSerializer(rule).data)
