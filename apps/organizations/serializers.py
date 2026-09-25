from rest_framework import serializers

from apps.organizations.models import Invitation, Membership, Organization, Project, Role


class OrganizationSerializer(serializers.ModelSerializer):
    # Slug is derived from the name on create (see OrganizationService); accepted
    # optionally so callers can pin one, but never required.
    slug = serializers.SlugField(required=False)

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "created_at"]
        read_only_fields = ["id", "created_at"]


class MembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "username", "role", "created_at"]
        read_only_fields = ["id", "username", "created_at"]


class InvitationWriteSerializer(serializers.Serializer):
    # By username, not id: a sequential id is guessable, and the invitee is
    # someone the admin can name.
    username = serializers.CharField(max_length=150)
    role = serializers.ChoiceField(choices=Role.choices, default=Role.MEMBER)


class InvitationSerializer(serializers.ModelSerializer):
    organization = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    invitee = serializers.CharField(source="invitee.username", read_only=True)
    # Null once the inviter's account is deleted (the invitation is then void).
    invited_by = serializers.CharField(
        source="invited_by.username", read_only=True, default=None
    )

    class Meta:
        model = Invitation
        fields = ["id", "organization", "invitee", "invited_by", "role", "status", "created_at"]
        read_only_fields = fields


class MembershipRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)


class ProjectSerializer(serializers.ModelSerializer):
    key = serializers.SlugField(required=False)

    class Meta:
        model = Project
        fields = ["id", "organization", "name", "key", "created_at"]
        read_only_fields = ["id", "organization", "created_at"]
