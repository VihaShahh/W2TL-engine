"""Shared selection fields for immutable domain contracts."""

from enum import StrEnum


class TransactionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"

class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"

class ClassificationType(StrEnum):
    FAILED = "failed"
    INTERNAL_TRANSFER = "internal_transfer"
    STAKING_REWARD = "staking_reward"
    AIRDROP = "airdrop"
    SPAM_AIRDROP = "spam_airdrop"
    WRAP = "wrap"
    SWAP = "swap"
    BUY = "buy"
    SELL = "sell"
    EXTERNAL_INBOUND = "external_inbound"
    EXTERNAL_OUTBOUND = "external_outbound"
    FEE = "fee"
    UNRECOGNIZED = "unrecognized"

class ResolutionState(StrEnum):
    RESOLVED = "resolved"
    REVIEW_FLAGGED = "review_flagged"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"

class EvidenceState(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"

class PriceResolutionState(StrEnum):
    FOUND = "found"
    MISSING = "missing"
    CONFLICT = "conflict"

class ExceptionSeverity(StrEnum):
    WARNING = "warning"
    EVENT_BLOCKING = "event_blocking"
    MONETARY_OUTPUT_BLOCKING = "monetary_output_blocking"
    RUN_FATAL = "run_fatal"

class PipelineStage(StrEnum):
    LOADING = "loading"
    NORMALIZATION = "normalization"
    CLASSIFICATION = "classification"
    ACCOUNTING = "accounting"
    REPORTING = "reporting"

class HoldingTerm(StrEnum):
    SHORT = "short"
    LONG = "long"

class CoverageStatus(StrEnum):
    RESOLVED = "resolved"
    REVIEW_FLAGGED = "review_flagged"
    QUARANTINED = "quarantined"
    SECONDARY_VIEW = "secondary_view"

class InventoryState(StrEnum):
    OPEN = "open"
    SUSPENSE = "suspense"
    QUARANTINED = "quarantined"

class FeeContext(StrEnum):
    ACQUISITION = "acquisition"
    DISPOSAL = "disposal"
    STANDALONE = "standalone"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
