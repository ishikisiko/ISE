## ADDED Requirements

### Requirement: Target-domain recovery SHALL continue deterministic fallback until coverage or exhaustion
For a plan-authorized target-domain recovery, the system SHALL continue through the
configured priority providers when an earlier provider returns no URL in the target's
allowed official domains.

#### Scenario: Primary provider returns only third-party pages
- **WHEN** the primary provider returns non-empty results but none is in the target official domains
- **THEN** the system SHALL record that attempt as insufficient target coverage
- **AND** it SHALL attempt the next plan-authorized provider in deterministic order

#### Scenario: Provider returns a target-domain page
- **WHEN** a provider returns at least one URL in the target official domains
- **THEN** the system SHALL retain only target-domain results for that recovery target
- **AND** it SHALL not invoke later fallback providers for that target
