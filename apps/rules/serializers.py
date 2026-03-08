from rest_framework import serializers

from apps.flags.models import FeatureFlag
from apps.rules.models import Rule


class RuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rule
        fields = [
            "id", "flag", "attribute", "operator",
            "value", "priority",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_flag(self, flag: FeatureFlag) -> FeatureFlag:
        """
        Prevent cross-user rule assignment.

        DRF resolves the 'flag' FK to a FeatureFlag instance before this
        method runs. We assert the resolved flag's owner matches the
        authenticated user — so a caller cannot attach a rule to another
        user's flag by supplying an arbitrary flag PK in the request body.

        Note: get_queryset() in the ViewSet restricts *reads*, but without
        this check a malicious POST/PUT could still write to any flag ID.

        We return the same generic message as a non-existent PK to avoid
        leaking information about other users' flag IDs.
        """
        request = self.context.get("request")
        if request is None:
            # Serializer used outside a request context (management commands,
            # unit tests without a request object): skip ownership check.
            return flag

        if flag.owner_id != request.user.id:
            raise serializers.ValidationError(
                "Invalid pk \u2014 flag not found or not accessible."
            )
        return flag
