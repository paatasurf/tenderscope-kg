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
}

PREFIX_TO_KIND: dict[str, str] = {v: k for k, v in UID_PREFIXES.items()}


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

    # Code-graph bridge (code entity references a business entity)
    CODE_REFERENCES  = "code_references"
