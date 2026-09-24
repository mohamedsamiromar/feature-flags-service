from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.accounts.models import User
from apps.environment.serializers import EnvironmentSerializer
from apps.organizations.serializers import OrganizationSerializer, ProjectSerializer


class RegisterSerializer(serializers.Serializer):
    """Input shape for ``POST /api/v1/auth/register/``.

    Username uniqueness is deliberately *not* checked here. It is a race the
    serializer cannot win — the gap between a `SELECT` and the `INSERT` is
    exactly where a concurrent signup lands — so the constraint is enforced by
    the database and translated by ``RegistrationService``, which sees the
    IntegrityError.
    """

    # Reuses the model's own validator rather than a bare CharField.
    # `create_user` does not call `full_clean`, so a model-level validator never
    # runs on this path — without it a username of "   " is accepted, and every
    # slug derived from it (org slug, project key) collapses to the same empty
    # base for every such signup.
    username = serializers.CharField(
        max_length=150, validators=[User.username_validator]
    )
    # Optional and not unique, matching Django's AbstractUser. Login is by
    # username, so an email is contact information here, not an identity.
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs: dict) -> dict:
        """Run Django's configured password validators.

        Field-level rather than service-level because password strength is a
        property of the submitted value, and a 400 with per-field errors is the
        right shape for it. The unsaved ``User`` is passed so
        ``UserAttributeSimilarityValidator`` can actually do its job — without
        it, "alice" would be an acceptable password for the user *alice*.
        """
        candidate = User(username=attrs["username"], email=attrs.get("email", ""))
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs


class RegisteredUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email"]


class RegistrationResponseSerializer(serializers.Serializer):
    """What a successful signup hands back.

    The JWT pair is included so registering leaves the caller able to make the
    next call. Without it every client would follow every signup with an
    immediate `POST /auth/token/` using credentials it already has.
    """

    user = RegisteredUserSerializer()
    organization = OrganizationSerializer()
    project = ProjectSerializer()
    environments = EnvironmentSerializer(many=True)
    access = serializers.CharField()
    refresh = serializers.CharField()
