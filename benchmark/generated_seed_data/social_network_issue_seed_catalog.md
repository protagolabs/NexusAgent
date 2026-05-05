# Social Network Issue Seed Catalog

Machine-readable catalog:

`benchmark/generated_seed_data/social_network_issue_seed_catalog.json`

This catalog is designed for realistic Social Network evaluation. The generated dialogue and generated QA should stay at the user level: people, aliases, relationships, roles, organizations, remembered expertise, and ordinary follow-up questions.

## Design Rules

- Dialogue seeds may create natural ambiguity through nicknames, paraphrases, late details, and separated role/domain descriptions.
- QA seeds must ask questions as a user would ask them after replay.
- QA seeds must not mention implementation details such as storage fields, database behavior, search modes, exact internal IDs, or expected failure mechanics.
- Internal issue labels and stress notes are for humans and test orchestration only; they should not be used as dialogue content.
- The generator intentionally avoids feeding `stress_signal` into the model prompt.
- `required_qa_phrases` lists ordinary user-facing phrases that must appear in the generated QA questions. This keeps the QA focused without exposing implementation details.

## Usage

Generate one case:

```bash
python scripts/generate_seed_dialogues.py --seed-catalog --case-id sn_composite_query_01 --turns 30
```

Generate a subset:

```bash
python scripts/generate_seed_dialogues.py --seed-catalog --case-id sn_entity_dedup_01,sn_search_doc_drift_01 --turns 30
```

Generate all catalog cases:

```bash
python scripts/generate_seed_dialogues.py --seed-catalog --turns 30
```

## Case Index

| Issue Family | Case IDs | User-Level Pressure |
|---|---|---|
| Semantic phrasing variants | `sn_keyword_semantic_duplicate_01`, `sn_keyword_semantic_duplicate_02`, `sn_keyword_semantic_duplicate_03` | The same contact is described through ordinary synonyms or adjacent role/domain phrases. |
| Many associations with late critical detail | `sn_keyword_cap_01`, `sn_keyword_cap_02`, `sn_keyword_cap_03` | A person or agent has many associations, then a late detail becomes the most important follow-up target. |
| Name and alias continuity | `sn_entity_dedup_01`, `sn_entity_dedup_02`, `sn_entity_dedup_03` | One person or agent is referred to through nickname, full name, honorific, initials, handle, or Matrix-style alias. |
| Re-describing known contacts | `sn_extraction_context_gap_01`, `sn_extraction_context_gap_02`, `sn_extraction_context_gap_03` | A known contact is described again with related but not identical wording. |
| Natural recall by name or description | `sn_search_doc_drift_01`, `sn_search_doc_drift_02`, `sn_search_doc_drift_03` | The user asks for a contact by ordinary name, remembered expertise, or organization description. |
| Composite role/domain recall | `sn_composite_query_01`, `sn_composite_query_02`, `sn_composite_query_03` | The user combines a role and domain that were mentioned separately in the dialogue. |
