-- CNT-T15: a decision wait is metadata, independent of the lifecycle state.
ALTER TABLE tasks ADD COLUMN waiting_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(waiting_json) AND json_type(waiting_json) = 'object');
