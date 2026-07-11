# Company Identity Contract

## Version

1.0.0 (Phase 1)

## Endpoint

`GET /api/v1/graph/companies/{uid}/identity`

The legacy prefix `GET /api/graph/companies/{uid}/identity` returns the same
shape and is also governed by this contract.

## Purpose

Provide a stable, read-only view of a canonical company's identity that is
independent of display names, source systems, or importer version.

## Contract Rules

1. **Immutable identifier**: `company_uid` is the only permanent identifier.
   It never changes and is never reused.
2. **Write-once deduplication**: `canonical_name` is set on creation and never
   updated.
3. **Mutable metadata**: `display_name`, `aliases`, `external_ids`, and
   `attributes` may change over time without changing identity.
4. **Additive only**: New optional fields may be added, but no existing field
   will be removed or change its type in a backward-incompatible way.

## Response Fields

| Field            | Type             | Required | Description                                                              |
|------------------|------------------|----------|--------------------------------------------------------------------------|
| `company_uid`    | string           | yes      | Permanent graph-native identifier, e.g. `CMP-00000001`.                  |
| `display_name`   | string           | yes      | Current human-readable name.                                             |
| `canonical_name` | string           | yes      | Normalized write-once deduplication key.                                 |
| `aliases`        | array of object  | yes      | Known alias names; each item contains `uid`, `name`, `confidence`, etc.  |
| `external_ids`   | object           | yes      | Map of external identifier keys (from `EXTERNAL_ID_KEYS`) to values.     |
| `attributes`     | object           | yes      | Arbitrary mutable metadata (city, province, phone, etc.).                |
| `merge_candidates`| array of object  | yes      | Confidence-scored `SAME_AS` candidates pending human confirmation.        |
| `source`         | string or null   | yes      | Importer that created the canonical record.                              |
| `confidence`     | number           | yes      | Data quality confidence of the canonical record.                         |

## Example

```json
{
  "company_uid": "CMP-00000001",
  "display_name": "TenderScope Inc.",
  "canonical_name": "tenderscope inc",
  "aliases": [],
  "external_ids": {},
  "attributes": {"city": "Vancouver"},
  "merge_candidates": [],
  "source": null,
  "confidence": 1.0
}
```

## Implementation

The Pydantic model `CompanyIdentityResponse` in
`src/tenderscope_kg/rest_server.py` enforces this contract for the REST
transport. The underlying data is assembled by `BizQueryEngine.company_identity()`
from the graph.
