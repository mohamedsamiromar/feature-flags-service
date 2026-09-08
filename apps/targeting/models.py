# No models.
#
# This app is the rule *evaluator* — `RuleEvaluator` in services.py — and owns
# no tables. Rules themselves live in `apps.rules`, segments in `apps.segments`.
#
# It previously carried `Country` and `City` scaffold models from an early
# geo-targeting sketch. Neither was ever referenced by the engine, and
# 0002_delete_scaffold_models drops them. The app stays in INSTALLED_APPS so
# that migration has somewhere to live.
