from apps.core.errors import APIError, Error
from apps.flags.services import FlagService
from apps.rules.models import Rule
from apps.rules.queries import RuleQuery


class RuleService:
    """Business logic for targeting rules.

    Enforces that a rule's flag is owned by the caller (get_queryset restricts
    reads, but a POST/PUT could otherwise write to any flag PK), and invalidates
    the flag's cached targeting config on every mutation.
    """

    def create(self, user, validated_data: dict) -> Rule:
        self._assert_flag_owned(validated_data["flag"], user)
        rule = RuleQuery.create(**validated_data)
        FlagService.invalidate_flag_caches(rule.flag)
        return rule

    def update(self, user, rule: Rule, validated_data: dict) -> Rule:
        self._assert_flag_owned(validated_data.get("flag", rule.flag), user)
        for attr, value in validated_data.items():
            setattr(rule, attr, value)
        RuleQuery.save(rule)
        FlagService.invalidate_flag_caches(rule.flag)
        return rule

    def delete(self, rule: Rule) -> None:
        flag = rule.flag  # unreachable after delete
        RuleQuery.delete(rule)
        FlagService.invalidate_flag_caches(flag)

    @staticmethod
    def _assert_flag_owned(flag, user) -> None:
        # Same generic message as a non-existent PK, to avoid leaking whether
        # another user's flag ID exists.
        if flag.owner_id != user.id:
            raise APIError(Error.INVALID_FLAG_REF)
