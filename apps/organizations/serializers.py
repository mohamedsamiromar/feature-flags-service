from rest_framework import serializers

from apps.organizations.models import Membership, Organization, Project, Role


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


class MembershipWriteSerializer(serializers.Serializer):
    user = serializers.IntegerField()
    role = serializers.ChoiceField(choices=Role.choices, default=Role.MEMBER)


class MembershipRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)


class ProjectSerializer(serializers.ModelSerializer):
    key = serializers.SlugField(required=False)

    class Meta:
        model = Project
        fields = ["id", "organization", "name", "key", "created_at"]
        read_only_fields = ["id", "organization", "created_at"]
