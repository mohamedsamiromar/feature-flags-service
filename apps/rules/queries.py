"""Query layer for the rules app — the only place with ORM access for rules."""

from apps.rules.models import Rule


class RuleQuery:
    @staticmethod
    def list_for_owner(user):
        # select_related("flag") lets cache-invalidation read flag.owner_id /
        # flag.key without extra queries.
        return Rule.objects.filter(flag__owner=user).select_related("flag")

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
