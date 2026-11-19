"""ARR-T03: Gemini token usage is read from the Interactions API names; no usage stays unknown, not 0."""
from types import SimpleNamespace

from multiagent.adapters.gemini import GeminiAdapter
from multiagent.catalog import ModelSpec
from multiagent.config import Settings
from multiagent.contracts import ContentOutput

from test_prompt_prefix import CONTENT_JSON


class _Interactions:
    def __init__(self, usage):
        self.usage = usage

    def create(self, **kwargs):
        return SimpleNamespace(output_text=CONTENT_JSON, steps=[], usage=self.usage, model="gemini-test")


def _call(tmp_path, usage):
    adapter = GeminiAdapter(Settings(_env_file=None, data_dir=tmp_path, gemini_api_key=""), client=SimpleNamespace(interactions=_Interactions(usage)))
    spec = ModelSpec(id="g", provider="gemini", model="gemini-test", tier="cloud", structured_output=True, web=False)
    return adapter.generate_structured(spec=spec, prompt="x", output_schema=ContentOutput)


def test_interactions_api_names_are_read(tmp_path):
    usage = SimpleNamespace(total_input_tokens=120, total_output_tokens=30, total_cached_tokens=64, total_tokens=150)
    response = _call(tmp_path, usage)
    assert (response.tokens_in, response.tokens_out) == (120, 30)
    assert response.metadata["cache_read_tokens"] == 64


def test_thought_tokens_count_as_output(tmp_path):
    usage = SimpleNamespace(total_input_tokens=10, total_output_tokens=5, total_thought_tokens=7)
    response = _call(tmp_path, usage)
    assert response.tokens_out == 12
    assert response.metadata["thought_tokens"] == 7


def test_previous_names_still_work(tmp_path):
    response = _call(tmp_path, SimpleNamespace(input_tokens=50, output_tokens=2, cached_content_token_count=32))
    assert (response.tokens_in, response.tokens_out) == (50, 2)
    assert response.metadata["cache_read_tokens"] == 32
    response = _call(tmp_path, SimpleNamespace(prompt_tokens=8, completion_tokens=3))
    assert (response.tokens_in, response.tokens_out) == (8, 3)


def test_new_names_win_over_old_ones(tmp_path):
    response = _call(tmp_path, SimpleNamespace(total_input_tokens=9, input_tokens=1, total_output_tokens=4, output_tokens=2))
    assert (response.tokens_in, response.tokens_out) == (9, 4)


def test_no_usage_is_unknown_not_a_count(tmp_path):
    for usage in (None, SimpleNamespace(), SimpleNamespace(total_input_tokens=None, total_output_tokens=None)):
        response = _call(tmp_path, usage)
        # 0 is the adapter's "unknown"; the engine stores tokens <= 0 as None (workflow_engine, response.tokens_in > 0).
        assert (response.tokens_in, response.tokens_out) == (0, 0)
        assert "cache_read_tokens" not in response.metadata

