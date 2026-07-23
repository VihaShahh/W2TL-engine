"""Shared selection fields for immutable domain contracts."""

from enum import StrEnum


class TransactionStatus(StrEnum):
    """Whether intended economic legs succeeded or reverted on-chain.

    A failed transaction can still carry separately proven charged-fee evidence.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"

class Direction(StrEnum):
    """Token movement relative to the wallet whose source record is being modeled.

    Direction is an observed component fact, not by itself a sale or acquisition classification.
    """

    INBOUND = "inbound"
    OUTBOUND = "outbound"

class ClassificationType(StrEnum):
    """Supported economic event families produced by evidence classification.

    The enum keeps later accounting dispatch explicit and provides an ``UNRECOGNIZED`` state
    instead of forcing unsupported evidence into the closest familiar category.
    """

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
    """Confidence and review outcome attached to an event classification.

    It separates a supported result from an unresolved, invalid, or review-flagged observation.
    """

    RESOLVED = "resolved"
    REVIEW_FLAGGED = "review_flagged"
    UNRESOLVED = "unresolved"
    INVALID = "invalid"

class EvidenceState(StrEnum):
    """Whether a basis, acquisition, or valuation fact is supported or unknown.

    Unknown objects retain exception identity and never expose a numeric placeholder.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"

class PriceResolutionState(StrEnum):
    """Cardinality outcome for one exact price-evidence query.

    A numeric price is available only when exactly one matching observation is found.
    """

    FOUND = "found"
    MISSING = "missing"
    CONFLICT = "conflict"

class ExceptionSeverity(StrEnum):
    """How strongly an unresolved fact blocks event, money, or the whole run.

    Severity lets expected review work coexist with valid output while contract failures stop it.
    """

    WARNING = "warning"
    EVENT_BLOCKING = "event_blocking"
    MONETARY_OUTPUT_BLOCKING = "monetary_output_blocking"
    RUN_FATAL = "run_fatal"

class PipelineStage(StrEnum):
    """Stable stage names locating where an exception was discovered.

    Stage identity helps reviewers distinguish malformed evidence from an accounting dependency.
    """

    LOADING = "loading"
    NORMALIZATION = "normalization"
    CLASSIFICATION = "classification"
    ACCOUNTING = "accounting"
    REPORTING = "reporting"

class HoldingTerm(StrEnum):
    """Short- or long-term result derived under the selected country policy.

    The original acquisition date is preserved across owned-wallet movements before this choice.
    """

    SHORT = "short"
    LONG = "long"

class CoverageStatus(StrEnum):
    """Final disposition of every stored source record in population coverage.

    Secondary and quarantined records remain counted even when they create no accounting event.
    """

    RESOLVED = "resolved"
    REVIEW_FLAGGED = "review_flagged"
    QUARANTINED = "quarantined"
    SECONDARY_VIEW = "secondary_view"

class InventoryState(StrEnum):
    """Whether represented quantity is selectable, suspended, or quarantined.

    Suspense removes uncertain outbound units from reuse without pretending the movement was a sale.
    """

    OPEN = "open"
    SUSPENSE = "suspense"
    QUARANTINED = "quarantined"

class FeeContext(StrEnum):
    """Parent-event context controlling one proven fee's accounting treatment.

    The explicit context prevents the same fee value from adjusting multiple results.
    """

    ACQUISITION = "acquisition"
    DISPOSAL = "disposal"
    STANDALONE = "standalone"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
