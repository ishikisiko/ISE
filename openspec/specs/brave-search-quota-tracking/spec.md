# brave-search-quota-tracking Specification

> **Status:** active — 当前契约，且在目标架构中存续。 分类依据见 `docs/agentic_loop_roadmap.md`。

## Purpose
TBD - created by archiving change replace-serpapi-with-brightdata-and-brave. Update Purpose after archive.
## Requirements
### Requirement: Brave Search enforces a configured per-key request rate
The system SHALL enforce a maximum request rate of 1 request per second for each configured Brave Search key slot.

#### Scenario: Primary key request rate is throttled
- **WHEN** the backend receives multiple Brave Search requests targeting the primary key slot within the same one-second window
- **THEN** the backend SHALL delay, reject, or otherwise throttle additional requests so that the primary key slot does not exceed 1 request per second

#### Scenario: Secondary key request rate is independently enforced
- **WHEN** Brave Search requests are sent through the secondary key slot
- **THEN** the backend SHALL apply the same 1 request per second limit independently to that secondary key slot

### Requirement: Brave Search usage is recorded on the backend
The system SHALL create a backend usage record for every Brave Search attempt so monthly quota consumption and failover behavior can be audited.

#### Scenario: Successful Brave request is recorded
- **WHEN** a Brave Search request completes successfully
- **THEN** the backend SHALL persist a usage record including the request timestamp, key slot, request outcome, and enough request identity to correlate quota consumption

#### Scenario: Failed Brave request is recorded
- **WHEN** a Brave Search request fails because of provider error, rate limit, authentication issue, or transport failure
- **THEN** the backend SHALL persist a usage record that marks the failure outcome and the key slot that was attempted

### Requirement: Brave Search uses explicit primary and secondary key roles
The system SHALL distinguish between a primary Brave Search key and a secondary Brave Search key in configuration, runtime routing, and usage records.

#### Scenario: Primary key is the default slot
- **WHEN** Brave Search is selected for a general web search request
- **THEN** the backend SHALL attempt the primary key slot first

#### Scenario: Secondary key is recorded as fallback usage
- **WHEN** the backend serves a request with the secondary Brave key after primary key failure or unavailability
- **THEN** the backend usage record SHALL mark that the secondary key slot was used as fallback

### Requirement: Brave usage data remains inspectable after process restart
The system SHALL persist Brave usage records in a backend-managed store that survives process restart.

#### Scenario: Usage history survives restart
- **WHEN** the backend process restarts after Brave Search requests have been recorded
- **THEN** previously recorded Brave usage entries SHALL remain available in the configured backend store for later inspection

