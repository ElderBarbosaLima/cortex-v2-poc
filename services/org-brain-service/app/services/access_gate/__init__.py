"""The access gate (Rev. B rule 4: constraints never lose).

- `applicability`: which approved rules apply to one case, decided by SQL over
  governed metadata (scope, entities, validity) and the rule's CEL condition,
  never by search, never by the case's words;
- `resolve`: per target, which applicable rule wins and which are overridden.
Search (issue 20) only adds knowledge on top; it can never add or remove a rule.
"""
