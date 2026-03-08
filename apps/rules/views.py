from rest_framework import permissions, viewsets

from apps.flags.services import FlagService
from apps.rules.models import Rule
from apps.rules.serializers import RuleSerializer


class RuleViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RuleSerializer

    def get_queryset(self):
        # Only rules belonging to the authenticated user's flags.
        # select_related("flag") is required so the cache-invalidation helpers
        # below can access flag.owner_id and flag.key without extra DB queries.
        return (
            Rule.objects
            .filter(flag__owner=self.request.user)
            .select_related("flag")
        )

    def perform_create(self, serializer):
        rule = serializer.save()
        # A newly created rule changes the flag's effective targeting config —
        # invalidate the cached flag data so the next evaluation re-reads from DB.
        FlagService._invalidate_cache(rule.flag.owner_id, rule.flag.key)

    def perform_update(self, serializer):
        rule = serializer.save()
        FlagService._invalidate_cache(rule.flag.owner_id, rule.flag.key)

    def perform_destroy(self, instance):
        # Capture identifiers before deletion; they become inaccessible afterwards.
        owner_id = instance.flag.owner_id
        flag_key = instance.flag.key
        instance.delete()
        FlagService._invalidate_cache(owner_id, flag_key)
