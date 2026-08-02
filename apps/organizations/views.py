from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.organizations.queries import (
    MembershipQuery,
    OrganizationQuery,
    ProjectQuery,
)
from apps.organizations.serializers import (
    MembershipRoleSerializer,
    MembershipSerializer,
    MembershipWriteSerializer,
    OrganizationSerializer,
    ProjectSerializer,
)
from apps.organizations.services import (
    MembershipService,
    OrganizationService,
    ProjectService,
)

_org_service = OrganizationService()
_membership_service = MembershipService()
_project_service = ProjectService()


class OrganizationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/organizations/                       — orgs the caller belongs to
    POST   /api/v1/organizations/                       — create (caller becomes OWNER)
    GET    /api/v1/organizations/{slug}/                — detail
    DELETE /api/v1/organizations/{slug}/                — delete (OWNER only)
    GET    /api/v1/organizations/{slug}/members/        — list members
    POST   /api/v1/organizations/{slug}/members/        — add member (ADMIN+)
    PATCH  /api/v1/organizations/{slug}/members/{uid}/  — change role (ADMIN+)
    DELETE /api/v1/organizations/{slug}/members/{uid}/  — remove member (ADMIN+)
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = OrganizationSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return OrganizationQuery.list_for_member(self.request.user)

    def perform_create(self, serializer):
        serializer.instance = _org_service.create(
            user=self.request.user, **serializer.validated_data
        )

    def destroy(self, request, *args, **kwargs):
        _org_service.delete(user=request.user, slug=kwargs[self.lookup_field])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, slug=None):
        if request.method == "POST":
            serializer = MembershipWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            membership = _membership_service.add(
                actor=request.user, slug=slug, **serializer.validated_data
            )
            return Response(
                MembershipSerializer(membership).data, status=status.HTTP_201_CREATED
            )

        org = OrganizationQuery.get_for_member(slug, request.user)
        qs = MembershipQuery.list_for_org(org)
        return Response(MembershipSerializer(qs, many=True).data)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"members/(?P<user_id>[^/.]+)",
    )
    def member_detail(self, request, slug=None, user_id=None):
        if request.method == "DELETE":
            _membership_service.remove(actor=request.user, slug=slug, user_id=user_id)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = MembershipRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = _membership_service.change_role(
            actor=request.user,
            slug=slug,
            user_id=user_id,
            role=serializer.validated_data["role"],
        )
        return Response(MembershipSerializer(membership).data)


class ProjectViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/projects/          — projects across the caller's orgs
    POST   /api/v1/projects/          — create under an org (body: organization slug, ADMIN+)
    GET    /api/v1/projects/{key}/    — detail
    DELETE /api/v1/projects/{key}/    — delete (ADMIN+)
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = ProjectSerializer
    lookup_field = "key"

    def get_queryset(self):
        return ProjectQuery.list_for_member(self.request.user)

    def create(self, request, *args, **kwargs):
        # `organization` here is the org slug the project should live under.
        slug = request.data.get("organization")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = _project_service.create(
            user=request.user,
            slug=slug,
            name=serializer.validated_data["name"],
            key=serializer.validated_data.get("key"),
        )
        return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        _project_service.delete(user=request.user, key=kwargs[self.lookup_field])
        return Response(status=status.HTTP_204_NO_CONTENT)
