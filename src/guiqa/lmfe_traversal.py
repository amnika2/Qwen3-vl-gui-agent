"""Equivalent sparse-trie traversal for LMFormatEnforcer 0.11.3."""

from lmformatenforcer.tokenenforcer import TokenEnforcer


def install_sparse_trie_traversal():
    original = TokenEnforcer._collect_allowed_tokens
    TokenEnforcer._collect_allowed_tokens = collect_allowed_tokens
    return original


def collect_allowed_tokens(self, parser, tree_node, allowed_tokens, shortcut_key):
    allowed_tokens.extend(tree_node.tokens)
    allowed_characters = parser.get_allowed_characters()
    children = tree_node.children
    # A sparse trie node needs a few membership tests, not an alphabet-sized scan.
    if len(children) <= 8:
        characters_to_explore = {character for character in children if character in allowed_characters}
    else:
        characters_to_explore = set(children).intersection(allowed_characters)

    if isinstance(shortcut_key, tuple) and shortcut_key[0] == "json_freetext":
        _, cur_len, min_len, max_len = shortcut_key
        cache = self.tokenizer_tree.json_freetext_tokens
        min_remaining = min(cache.max_token_len, max(0, min_len - cur_len))
        max_allowed_len = min(cache.max_token_len, max_len - cur_len)
        allowed_tokens.extend(cache.lookup_allowed_tokens(min_remaining, max_allowed_len).allowed_tokens)
        characters_to_explore = characters_to_explore.intersection(['"'])

    for character in characters_to_explore:
        next_parser = parser.add_character(character)
        collect_allowed_tokens(self, next_parser, children[character], allowed_tokens, None)
