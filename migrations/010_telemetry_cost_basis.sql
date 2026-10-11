-- ECO-T03: evidence behind an estimated cost. JSON with the model id, the price source, its validity
-- window and provenance, and the cost with its currency (the currency matters when it is not USD,
-- because estimated_cost_usd stays NULL then). NULL means no estimate was possible, including every
-- row recorded before this migration.
ALTER TABLE telemetry ADD COLUMN cost_basis TEXT;
