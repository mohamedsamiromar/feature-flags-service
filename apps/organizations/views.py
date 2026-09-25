from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.organizations.queries import (
    InvitationQuery,
    MembershipQuery,
    OrganizationQuery,
    ProjectQuery,
)
from apps.organizations.serializers import (
    InvitationSerializer,
    InvitationWriteSerializer,
    MembershipRoleSerializer,
    MembershipSerializer,
    OrganizationSerializer,
    ProjectSerializer,
)
from apps.organizations.services import (
    InvitationService,
    MembershipService,
    OrganizationService,
    ProjectService,
)

_org_service = OrganizationService()
_membership_service = MembershipService()
_invitation_service = InvitationService()
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
    GET    /api/v1/organizations/{slug}/members/            — list members
    PATCH  /api/v1/organizations/{slug}/members/{uid}/      — change role (ADMIN+)
    DELETE /api/v1/organizations/{slug}/members/{uid}/      — remove member (ADMIN+)
    GET    /api/v1/organizations/{slug}/invitations/        — pending invitations (ADMIN+)
    POST   /api/v1/organizations/{slug}/invitations/        — invite by username (ADMIN+)
    DELETE /api/v1/organizations/{slug}/invitations/{id}/   — revoke (ADMIN+)

    There is no POST to members/: a user joins by accepting an invitation
    (see InvitationViewSet), never by being added.
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

    @action(detail=True, methods=["get"], url_path="members")
    def members(self, request, slug=None):
        org = OrganizationQuery.get_for_member(slug, request.user)
        qs = MembershipQuery.list_for_org(org)
        return Response(MembershipSerializer(qs, many=True).data)

    @action(detail=True, methods=["get", "post"], url_path="invitations")
    def invitations(self, request, slug=None):
        if request.method == "POST":
            serializer = InvitationWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            invitation = _invitation_service.invite(
                actor=request.user, slug=slug, **serializer.validated_data
            )
            return Response(
                InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED
            )

        qs = _invitation_service.list_for_org(actor=request.user, slug=slug)
        return Response(InvitationSerializer(qs, many=True).data)

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"invitations/(?P<invitation_id>[^/.]+)",
    )
    def invitation_detail(self, request, slug=None, invitation_id=None):
        _invitation_service.revoke(
            actor=request.user, slug=slug, invitation_id=invitation_id
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

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


class InvitationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """
    The invitee's side. Another user's invitation is a 404, as is a decided one.

    GET    /api/v1/invitations/               — my pending invitations
    POST   /api/v1/invitations/{id}/accept/   — join the organization
    POST   /api/v1/invitations/{id}/decline/  — refuse
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = InvitationSerializer

    def get_queryset(self):
        return InvitationQuery.pending_for_invitee(self.request.user)

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        invitation = _invitation_service.accept(user=request.user, invitation_id=pk)
        return Response(InvitationSerializer(invitation).data)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        invitation = _invitation_service.decline(user=request.user, invitation_id=pk)
        return Response(InvitationSerializer(invitation).data)


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
