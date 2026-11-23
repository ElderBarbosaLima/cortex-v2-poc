# PR 037 — The six failure scenarios as the first golden tests

Branch: `poc/037-six-scenarios` → `main` · Issue: [docs/tasks.md §37](../tasks.md) · From Nake's review

## What
- **`tests/integration/test_scenarios.py`** holds one named test per Rev. B scenario, numbered as in the architecture. **`make scenarios`** runs them, and **`make test-all` now runs them first.** Scenarios 2, 3 and 4 also keep running on the real data of both workflows (golden sets, `make e2e`).
- **Scenario 1: an import of an expired offer.**
  - **`POST /v1/cortex/imports`** (`import_records`): a batch from a source system goes through the admission gate item by item, and needs the importer as `X-End-User-Id`.
  - **An import never approves:** every new item is `proposed`, with authority capped at 10 (the requested authority is kept in `source`) until an owner decides.
  - **Same content and same validity as an approved record:** the item is a `duplicate` and nothing is written.
  - **Same content, other validity:** the item becomes a proposal that `supersedes` the approved record, so **an approval is never renewed or extended silently**.
  - **An item already past `valid_to`** is flagged `expired` and never applies, even if approved.
  - A refused item does not stop the others.
- **Scenario 5: replay after a model or index change.**
  - Every served pack records **what built it**, as `built_with {projection, embedding_model}` (migration `012`, with `SearchProjection.version`).
  - A pack requested again is the stored one. A fresh pack is a new pack and says what built it.
- **Scenario 6: one conflict met by hundreds of jobs.** 200 corrections on 200 work cases make **one** review case, with 200 proposals and all 200 affected cases visible in the case detail.

## Acceptance criteria
```
$ make scenarios
PASSED test_scenario_1_an_import_never_renews_an_approval_or_makes_an_expired_offer_apply
       # re-import of the approved offer → duplicate (valid_to unchanged); with a later valid_to → proposed, supersedes the offer;
       # expired item → proposed, expired=true; approved by the owner, still in no current pack
PASSED test_scenario_2_a_local_instruction_cannot_beat_a_global_prohibition    # local authority 40 vs global prohibition 10: overridden_by the prohibition
PASSED test_scenario_3_a_mandatory_rule_missed_by_search_is_still_supplied     # constraint, never a search candidate
PASSED test_scenario_4_a_rule_withdrawn_while_the_projection_lags_is_caught_at_release
PASSED test_scenario_5_after_a_model_or_index_change_the_stored_pack_is_returned   # replay == served (embedding_model null); fresh pack: fake/embedding-16
PASSED test_scenario_6_one_conflict_met_by_hundreds_of_jobs_is_one_review_case     # 200 corrections → 1 case, 200 affected cases
6 passed
```
`make lint`, `make typecheck` and `make test` pass, and `make test-integration` gives 131 passed.

**Found while testing.** In my first version, a re-import with a longer validity counted as a "duplicate", because only the content was compared and validity is not part of the content. That is exactly the silent renewal scenario 1 is about. The new validity would have been lost without anyone approving it. Now a duplicate needs the same validity too, and a different validity becomes a supersede proposal for an owner to decide.

The contract gains `import_records`, `ContextPack.built_with` and `ImportItemResult.supersedes`. The clean-clone run covers the whole batch: see PR 032.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
