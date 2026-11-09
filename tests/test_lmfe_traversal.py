import json

import pytest

pytest.importorskip("lmformatenforcer")

from lmformatenforcer import JsonSchemaParser
from lmformatenforcer.tokenenforcer import TokenEnforcer, TokenEnforcerTokenizerData
from lmformatenforcer.tokenlist import TokenList

from guiqa.lmfe_traversal import collect_allowed_tokens
from guiqa.review_contract import json_schema


@pytest.fixture(scope="module")
def data():
    strings = list(dict.fromkeys(list('{}[],:" \\nrt0123456789abcdef') + [
        '"field"', '"before"', '"after"', '"observation"', '"Title"', '"pass"',
        'true', 'false', 'null', '"value": "', '"step": 3', '"}, {"',
        'a', 'abcd', '"a"', 'a"', '\\"', '\\u00ab', '雪', 'é', '\n', '\t',
        ' ' * 12, ' ' * 13, '"a", "', '"after": null, "before": {',
    ]))
    tokens = [(index, text, True) for index, text in enumerate(strings)]
    return TokenEnforcerTokenizerData(tokens, lambda ids: ''.join(strings[i] for i in ids),
                                     len(strings), False, len(strings) + 1)


@pytest.mark.parametrize("prefix", [
    '', '{', '{"observation":', '{"observation": "', '{"observation": "a',
    '{"observation": "a"', '{"observation": "a",',
    '{"field_readings": [{"field": "Title", "before":',
    '{"field_readings": [{"field": "Title", "before": {"value": "',
    '{"field_readings": [{"field": "Title", "before": {"value": "a", "step":',
    '{"result_steps": [3', '{"submission": {"status": "accepted", "evidence":',
    '{"observation": "' + 'a' * 511,
    '{"observation": "' + 'a' * 512,
    '{"observation": ' + ' ' * 12,
])
def test_sparse_traversal_preserves_allowed_token_set(data, prefix):
    enforcer = TokenEnforcer(data, JsonSchemaParser(json_schema([0, 3, 4, 7])))
    parser = enforcer.root_parser
    for character in prefix:
        parser = parser.add_character(character)
    original, optimized = TokenList(False, data.vocab_size), TokenList(False, data.vocab_size)
    enforcer._collect_allowed_tokens(parser, data.tokenizer_tree.root, original, parser.shortcut_key())
    collect_allowed_tokens(enforcer, parser, data.tokenizer_tree.root, optimized, parser.shortcut_key())
    assert set(original.allowed_tokens) == set(optimized.allowed_tokens)


def test_sparse_traversal_preserves_tokens_across_mixed_nullable_states(data):
    schema = {"anyOf": [{"type": "string", "maxLength": 5}, {"type": "null"}]}
    enforcer = TokenEnforcer(data, JsonSchemaParser(schema))
    for prefix in ('', ' ', '"', '"ab', 'n', 'nu', 'null'):
        parser = enforcer.root_parser
        for character in prefix:
            parser = parser.add_character(character)
        expected, actual = TokenList(False, data.vocab_size), TokenList(False, data.vocab_size)
        enforcer._collect_allowed_tokens(parser, data.tokenizer_tree.root, expected, parser.shortcut_key())
        collect_allowed_tokens(enforcer, parser, data.tokenizer_tree.root, actual, parser.shortcut_key())
        assert set(expected.allowed_tokens) == set(actual.allowed_tokens), json.dumps(prefix)
