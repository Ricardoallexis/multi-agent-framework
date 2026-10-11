-- ORQ-4 group lifecycle is independent from authorization.
ALTER TABLE task_groups ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'open'
    CHECK (lifecycle_state IN ('open', 'closed'));
ALTER TABLE task_groups ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;

-- Group writers that do not own lifecycle policy still advance the optimistic revision.
CREATE TRIGGER task_groups_revision_on_mutation
AFTER UPDATE OF title, authorized, default_requires, default_gate_json, legacy_release_mode, lifecycle_state
ON task_groups
WHEN NEW.revision = OLD.revision AND (
    NEW.title IS NOT OLD.title OR NEW.authorized IS NOT OLD.authorized OR
    NEW.default_requires IS NOT OLD.default_requires OR NEW.default_gate_json IS NOT OLD.default_gate_json OR
    NEW.legacy_release_mode IS NOT OLD.legacy_release_mode OR NEW.lifecycle_state IS NOT OLD.lifecycle_state
)
BEGIN
    UPDATE task_groups SET revision=OLD.revision+1 WHERE id=NEW.id;
END;
