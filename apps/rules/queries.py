"""Query layer for the rules app — the only place with ORM access for rules."""

from apps.rules.models import Rule


class RuleQuery:
    @staticmethod
    def list_for_member(user):
        # select_related("flag") lets cache-invalidation read flag.project_id /
        # flag.key without extra queries. distinct() guards against row fan-out
        # from the membership join.
        return (
            Rule.objects
            .filter(flag__project__organization__memberships__user=user)
            .select_related("flag")
            .distinct()
        )

    @staticmethod
    def create(**fields) -> Rule:
        return Rule.objects.create(**fields)

    @staticmethod
    def save(rule: Rule, update_fields=None) -> Rule:
        rule.save(update_fields=update_fields)
        return rule

    @staticmethod
    def delete(rule: Rule) -> None:
        rule.delete()
