## ADDED Requirements

### Requirement: Official coverage SHALL be evaluated per configured comparison target
The evidence policy SHALL require retained official evidence with a pricing signal for
every mapped target before classifying an authority-required pricing comparison with
configured official-domain targets as verified.

#### Scenario: One target remains without official evidence
- **WHEN** a pricing comparison has official evidence for one mapped member but not another
- **THEN** verification SHALL report the missing target as an authority/comparison gap
- **AND** the response SHALL not present a normal verified price comparison

#### Scenario: All mapped targets have official evidence
- **WHEN** every mapped comparison member has retained official evidence with a pricing signal
- **THEN** verification SHALL allow normal answer generation subject to the remaining plan policies

#### Scenario: Official homepage lacks pricing information
- **WHEN** a target-domain result is an official homepage but its retained content lacks a pricing signal
- **THEN** verification SHALL keep that target missing for the pricing comparison
