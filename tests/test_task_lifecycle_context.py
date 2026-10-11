from multiagent.config import Settings
from multiagent.services import ApplicationServices


def test_context_fingerprint_changes_for_group_lifecycle_and_revision(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                        gemini_api_key='', openai_api_key='', openai_enabled=False)
    services = ApplicationServices.create(settings, dry_run=True)
    tasks = services.tasks
    tasks.create_group('G', 'Lifecycle context', actor='Codex', authorized=True)
    tasks.create_task('G', 'T', 'Task', actor='Codex')
    for action in ('claim', 'start', 'submit'):
        tasks.transition('T', action, actor='Codex')
    tasks.record_delivery_fact('T', delivery_ref='doc://fixtures/delivery', delivery_sha256='a' * 64,
                               has_patch=False, patchset_sha256='', actor='Codex')
    original_v1 = services.envelopes.get_envelope('T')
    initial = services.envelopes.get_context_v2('T')
    assert initial['release_context']['group_policy']['lifecycle_state'] == 'open'
    assert initial['release_context']['group_policy']['revision'] == 1
    tasks.set_group_state('G', 'closed', actor='Codex', cause='all delivered', expected_revision=1)
    closed = services.envelopes.get_context_v2('T', known_fingerprint=initial['fingerprint'])
    assert not closed.get('unchanged')
    assert closed['release_context']['group_policy']['lifecycle_state'] == 'closed'
    assert closed['release_context']['group_policy']['revision'] == 2
    assert closed['fingerprint'] != initial['fingerprint']
    tasks.set_group_state('G', 'open', actor='Codex', cause='follow-up', expected_revision=2)
    reopened = services.envelopes.get_context_v2('T', known_fingerprint=closed['fingerprint'])
    assert not reopened.get('unchanged')
    assert reopened['release_context']['group_policy']['lifecycle_state'] == 'open'
    assert reopened['fingerprint'] != initial['fingerprint']
    final_v1 = services.envelopes.get_envelope('T')
    assert {k: v for k, v in final_v1.items() if k != 'events_after'} == {
        k: v for k, v in original_v1.items() if k != 'events_after'}
    assert services.envelopes.get_context_v2('T', known_fingerprint=reopened['fingerprint']) == {
        'unchanged': True, 'fingerprint': reopened['fingerprint']}