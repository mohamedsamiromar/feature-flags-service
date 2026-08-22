from apps.flags.services import FlagService
from apps.organizations.services import AccessService
from apps.core.errors import APIError, Error
from apps.rules.models import Operator, Rule
from apps.rules.queries import RuleQuery
from apps.segments.queries import SegmentQuery


class RuleService:
    """Business logic for targeting rules.

    Enforces that the caller can write to the rule's flag through project
    membership (get_queryset restricts reads, but a POST/PUT could otherwise
    write to any flag PK), and invalidates the flag's cached targeting config on
    every mutation. A flag in a project the caller is not a member of surfaces
    as a 404; a member without write role gets a 403.
    """

    def create(self, user, validated_data: dict) -> Rule:
        flag = validated_data["flag"]
        self._assert_flag_writable(flag, user)
        self._assert_segment_exists(flag, validated_data)
        self._assert_attribute_present(validated_data)
        rule = RuleQuery.create(**validated_data)
        FlagService.invalidate_flag_caches(rule.flag)
        return rule

    def update(self, user, rule: Rule, validated_data: dict) -> Rule:
        flag = validated_data.get("flag", rule.flag)
        self._assert_flag_writable(flag, user)
        self._assert_segment_exists(flag, validated_data, current=rule)
        self._assert_attribute_present(validated_data, current=rule)
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

    @staticmethod
    def _assert_segment_exists(flag, validated_data: dict, current: Rule = None) -> None:
        """For a segment rule, `value` holds a segment key rather than a literal.

        Checked here rather than in the serializer because it is a cross-entity
        question (does this key name a segment in *this flag's* project?). A
        typo would otherwise create a rule that silently matches nobody.
        """
        operator = validated_data.get("operator", current.operator if current else None)
        if operator not in Operator.segment_operators():
            return

        key = validated_data.get("value", current.value if current else None)
        if not SegmentQuery.exists_with_key(flag.project, key):
            raise APIError(Error.UNKNOWN_SEGMENT, extra=[key])

    @staticmethod
    def _assert_attribute_present(validated_data: dict, current: Rule = None) -> None:
        """`attribute` is optional only for segment operators.

        The model allows blank so segment rules can omit it; every other
        operator compares a named context attribute and is meaningless without
        one — a blank attribute there would silently match nobody.
        """
        operator = validated_data.get("operator", current.operator if current else None)
        if operator in Operator.segment_operators():
            return

        attribute = validated_data.get("attribute", current.attribute if current else "")
        if not attribute:
            raise APIError(Error.REQUIRED_FIELD)
