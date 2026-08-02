from apps.flags.services import FlagService
from apps.organizations.services import AccessService
from apps.rules.models import Rule
from apps.rules.queries import RuleQuery


class RuleService:
    """Business logic for targeting rules.

    Enforces that the caller can write to the rule's flag through project
    membership (get_queryset restricts reads, but a POST/PUT could otherwise
    write to any flag PK), and invalidates the flag's cached targeting config on
    every mutation. A flag in a project the caller is not a member of surfaces
    as a 404; a member without write role gets a 403.
    """

    def create(self, user, validated_data: dict) -> Rule:
        self._assert_flag_writable(validated_data["flag"], user)
        rule = RuleQuery.create(**validated_data)
        FlagService.invalidate_flag_caches(rule.flag)
        return rule

    def update(self, user, rule: Rule, validated_data: dict) -> Rule:
        self._assert_flag_writable(validated_data.get("flag", rule.flag), user)
        for attr, value in validated_data.items():
            setattr(rule, attr, value)
        RuleQuery.save(rule)
        FlagService.invalidate_flag_caches(rule.flag)
        return rule

    def delete(self, user, rule: Rule) -> None:
        self._assert_flag_writable(rule.flag, user)
        flag = rule.flag  # unreachable after delete
        RuleQuery.delete(rule)
        FlagService.invalidate_flag_caches(flag)

    @staticmethod
    def _assert_flag_writable(flag, user) -> None:
        AccessService.assert_can_write(user, flag.project)
