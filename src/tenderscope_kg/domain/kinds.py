"""
TenderScope Knowledge Graph — Entity and relation kind enumerations.

Also exports canonicalize() — the single normalization function used by ALL
repository implementations for deduplication.  Having it here ensures every
backend deduplicates identically.
"""
from __future__ import annotations

import re
from enum import Enum


def canonicalize(name: str) -> str:
    """
    Stable normalization for entity deduplication.

    Rules (identical across all repository backends):
      - Strip leading/trailing whitespace
      - Collapse internal whitespace runs to a single space
      - Lowercase

    This function is the deduplication key.  Two names that produce the same
    canonical form are considered the same entity within the same kind.

    Used by: BizRepositorySQLite, BizRepositoryPG, FakeBizRepository, and
    any caller that needs to predict canonical_name before inserting.
    """
    return re.sub(r"\s+", " ", name.strip().lower())


UID_PREFIXES: dict[str, str] = {
    "company":      "CMP",
    "tender":       "TEN",
    "person":       "PER",
    "address":      "ADR",
    "phone":        "PHN",
    "email":        "EML",
    "website":      "WEB",
    "license":      "LIC",
    "project":      "PRJ",
    "organization": "ORG",
    "document":     "DOC",
    "province":     "PRV",
    "city":         "CTY",
    "industry":     "IND",
    "naics":        "NAI",
    "equipment":    "EQP",
    "contract":     "CON",
    "permit":       "PRM",
    "company_alias": "ALI",
}

PREFIX_TO_KIND: dict[str, str] = {v: k for k, v in UID_PREFIXES.items()}


# ── External identifier keys ───────────────────────────────────────────────────
# Canonical attribute key names for all known external identifiers attached to
# a COMPANY entity.  Stored in BizEntity.attributes under these exact keys.
# They are metadata — they never change company_uid.
# Adding new identifiers later requires only adding a key here: no schema
# change, no migration, no UID change.
EXTERNAL_ID_KEYS: dict[str, str] = {
    # Canadian government
    "bc_registry":          "id_bc_registry",
    "business_number":      "id_business_number",
    "gst_number":           "id_gst_number",
    "cra_number":           "id_cra_number",
    "pspc_vendor":          "id_pspc_vendor",
    # International / financial
    "duns":                 "id_duns",
    "lei":                  "id_lei",
    # Web / digital presence
    "website":              "id_website",
    "domain":               "id_domain",
    "linkedin_company":     "id_linkedin_company",
    "twitter":              "id_twitter",
    "facebook":             "id_facebook",
    # TenderScope-internal cross-references
    "scraper_id":           "scraper_id",
    "canonical_company_id": "canonical_company_id",
}


class BizEntityKind(str, Enum):
    COMPANY      = "company"
    TENDER       = "tender"
    PERSON       = "person"
    ADDRESS      = "address"
    PHONE        = "phone"
    EMAIL        = "email"
    WEBSITE      = "website"
    LICENSE      = "license"
    PROJECT      = "project"
    ORGANIZATION = "organization"
    DOCUMENT     = "document"
    PROVINCE     = "province"
    CITY         = "city"
    INDUSTRY     = "industry"
    NAICS        = "naics"
    EQUIPMENT    = "equipment"
    CONTRACT     = "contract"
    PERMIT       = "permit"
    COMPANY_ALIAS = "company_alias"


class BizRelationKind(str, Enum):
    # Ownership / structure
    OWNS             = "owns"
    OWNED_BY         = "owned_by"
    PARENT_OF        = "parent_of"
    SUBSIDIARY_OF    = "subsidiary_of"
    MEMBER_OF        = "member_of"

    # People
    EMPLOYS          = "employs"
    EMPLOYED_BY      = "employed_by"
    MANAGED_BY       = "managed_by"
    MANAGES          = "manages"
    CONTACT_FOR      = "contact_for"

    # Business relationships
    WORKS_WITH       = "works_with"
    AWARDED_TO       = "awarded_to"
    SUBMITTED_BID    = "submitted_bid"
    AWARDED_BY       = "awarded_by"
    LICENSED_BY      = "licensed_by"
    LICENSES         = "licenses"

    # Location
    LOCATED_AT       = "located_at"
    HAS_ADDRESS      = "has_address"
    IN_CITY          = "in_city"
    IN_PROVINCE      = "in_province"

    # References / dependencies
    REFERENCES       = "references"
    RELATED_TO       = "related_to"
    DEPENDS_ON       = "depends_on"
    USES             = "uses"

    # Contact info
    HAS_PHONE        = "has_phone"
    HAS_EMAIL        = "has_email"
    HAS_WEBSITE      = "has_website"

    # Documents / permits / contracts
    HAS_DOCUMENT     = "has_document"
    HAS_PERMIT       = "has_permit"
    HAS_CONTRACT     = "has_contract"
    HAS_LICENSE      = "has_license"

    # Industry classification
    IN_INDUSTRY      = "in_industry"
    HAS_NAICS        = "has_naics"

    # Equipment / resources
    OWNS_EQUIPMENT   = "owns_equipment"

    # Tender issuance (buyer/organization → tender)
    ISSUED_BY        = "issued_by"
    ISSUES           = "issues"

    # Permits
    APPLIED_FOR      = "applied_for"
    CONTRACTED_FOR   = "contracted_for"

    # Participation (bid, application, etc.)
    PARTICIPATED_IN  = "participated_in"

    # Identity resolution
    ALIAS_OF         = "alias_of"
    # Confidence-scored merge candidate: two COMPANY nodes believed to be the
    # same real company.  attributes must include 'confidence', 'reason', and
    # 'evidence' (list of dicts).  A SAME_AS edge is bidirectional by
    # convention — create both directions or treat as undirected.
    SAME_AS          = "same_as"

    # Code-graph bridge (code entity references a business entity)
    CODE_REFERENCES  = "code_references"
