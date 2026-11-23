"""Bank payer names → customers (issue 26).

Japanese bank transfers carry the payer's name in half-width kana (全銀 format),
with the legal form as a marker before or after it (`ｶ)ｶｸｳﾃﾞﾝｷ`, `ｶｸｳﾃﾞﾝｷ(ｶ`,
or spelled out, `ｶﾌﾞｼｷｶﾞｲｼｬｶｸｳﾃﾞﾝｷ`), small kana written large (`ｼﾖｳｼﾞ` for
ショウジ) and names truncated to fit the field. The normaliser maps all of
that to one core name; the resolver compares it with each customer's
registered bank names:

- equal raw form → `high` (`exact`); equal core, other legal-form spelling →
  `high` (`corporate_suffix_form`);
- a truncation that only one registered name starts with → `high`
  (`truncated_prefix`); one that several start with → `ambiguous`
  (`prefix_matches_several`): never an automatic match;
- one edit away from exactly one name → `low` (`edit_distance_1`);
- nothing → `none`.
Only `high` names a customer. Anything else goes to a person.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_SMALL_TO_LARGE = str.maketrans("ァィゥェォャュョッヮヵヶ", "アイウエオヤユヨツワカケ")
# Legal-form markers, after normalisation: prefix and suffix abbreviations
# (株式会社 カ, 合同会社 ド, 有限会社 ユ) and the spelled-out forms.
_PREFIXES = ("カ)", "ド)", "ユ)", "カブシキガイシヤ", "ゴウドウガイシヤ", "ユウゲンガイシヤ")
_SUFFIXES = ("(カ", "(ド", "(ユ", "カブシキガイシヤ", "ゴウドウガイシヤ", "ユウゲンガイシヤ")
MIN_PREFIX = 4  # a shorter truncation says too little


def normalise(raw: str) -> str:
    """Half/full width folded (NFKC), small kana made large, spaces dropped."""
    text = unicodedata.normalize("NFKC", raw).translate(_SMALL_TO_LARGE)
    return "".join(ch for ch in text if not ch.isspace() and ch not in "・.-")


def core(raw: str) -> str:
    """The name without its legal-form marker."""
    name = normalise(raw)
    for prefix in _PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _one_edit(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


@dataclass(frozen=True)
class Resolution:
    customer_id: str | None  # only when band == "high"
    band: str  # high | low | ambiguous | none
    evidence: str
    candidates: tuple[str, ...]
    normalised: str


def resolve(raw: str, registered: Mapping[str, Iterable[str]]) -> Resolution:
    """`registered`: customer_id -> the bank names it is registered under."""
    name, payer = normalise(raw), core(raw)
    exact = sorted(c for c, names in registered.items() if name in {normalise(n) for n in names})
    if len(exact) == 1:
        return Resolution(exact[0], "high", "exact", tuple(exact), name)
    same_core = sorted(c for c, names in registered.items() if payer in {core(n) for n in names})
    if len(same_core) == 1:
        return Resolution(same_core[0], "high", "corporate_suffix_form", tuple(same_core), name)
    if len(exact) > 1 or len(same_core) > 1:
        return Resolution(None, "ambiguous", "same_name_several", tuple(exact or same_core), name)
    if len(payer) >= MIN_PREFIX:
        prefixed = sorted(
            c for c, names in registered.items() if any(core(n).startswith(payer) for n in names)
        )
        if len(prefixed) == 1:
            return Resolution(prefixed[0], "high", "truncated_prefix", tuple(prefixed), name)
        if len(prefixed) > 1:
            return Resolution(None, "ambiguous", "prefix_matches_several", tuple(prefixed), name)
        near = sorted(
            c for c, names in registered.items() if any(_one_edit(payer, core(n)) for n in names)
        )
        if len(near) == 1:
            return Resolution(None, "low", "edit_distance_1", tuple(near), name)
        if len(near) > 1:
            return Resolution(None, "ambiguous", "edit_distance_1_several", tuple(near), name)
    return Resolution(None, "none", "no_registered_name", (), name)
