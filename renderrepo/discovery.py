"""Small deterministic task vocabulary and ranking, shared with the browser."""
import re
import unicodedata

# Canonical task words, not a general stemmer. Extend with examples and tests.
VOCABULARY = {
    'photograph': 'photo', 'photographs': 'photo', 'photos': 'photo',
    'picture': 'photo', 'pictures': 'photo', 'image': 'photo', 'images': 'photo',
    'touchup': 'repair', 'touchups': 'repair', 'fixes': 'repair', 'repaired': 'repair', 'restorations': 'repair',
    'fix': 'repair', 'fixing': 'repair', 'fixed': 'repair', 'restore': 'repair',
    'restoring': 'repair', 'restored': 'repair', 'restoration': 'repair',
    'repairs': 'repair', 'repairing': 'repair', 'retouch': 'repair', 'retouching': 'repair',
    'scratches': 'scratch', 'scratched': 'scratch', 'portraits': 'portrait',
    'backgrounds': 'background', 'removing': 'remove', 'removal': 'remove',
    'upscaling': 'upscale', 'enlarge': 'upscale', 'enlarging': 'upscale',
    'videos': 'video', 'colour': 'color', 'colourize': 'colorize', 'colorization': 'colorize',
}
STOP_WORDS = {'a', 'an', 'the', 'i', 'want', 'to', 'my', 'please', 'of', 'and', 'with'}


def validate_discovery(value):
    if not isinstance(value, dict) or set(value) - {'title', 'summary', 'tasks', 'tags', 'required_inputs', 'examples', 'limitations', 'actions'}:
        raise ValueError('discovery must contain title/summary or lists of tasks, tags, required_inputs, examples, limitations')
    for key, item in value.items():
        if key in {'title', 'summary'}:
            if not isinstance(item, str) or len(item) > 2000:
                raise ValueError(f'discovery {key} must be text, at most 2000 characters')
        elif (not isinstance(item, list) or len(item) > 50 or
              not all(isinstance(s, str) and 0 < len(s.strip()) <= 2000 for s in item)):
            raise ValueError(f'discovery {key} must be a list of up to 50 nonempty text items')
    from .capabilities import ACTIONS
    if any(a not in ACTIONS for a in value.get('actions', [])):
        raise ValueError('discovery actions must be stable workflow action identifiers')
    return value


def tokens(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = re.sub(r'\btouch[\W_]*up\b', 'repair', text)
    return set(VOCABULARY.get(t, t) for t in re.findall(r'[^\W_]+', text) if t not in STOP_WORDS)


def search_fields(item):
    meta = item.get('discovery', {})
    from .capabilities import ACTIONS
    c = item.get('capabilities', {})
    return [*( (4, t) for t in meta.get('tasks', [])),
            (3, c.get('purpose', '')), *((3, p['label']) for p in c.get('processing', []) if p.get('exposed')),
            *((2, ACTIONS[a]) for a in c.get('actions', []) if a in ACTIONS),
            (3, ' '.join(meta.get('tags', []))), (2, meta.get('title', '')),
            (1, meta.get('summary', '')), (1, item.get('description', '')), (1, item['name'])]


def score(item, query):
    words = tokens(query)
    if not words:
        return 1
    fields = [(tier, tokens(text)) for tier, text in search_fields(item)]
    if not words <= set().union(*(terms for _, terms in fields)):
        # Preserve legacy partial-name/description lookup, below every task match.
        fallback = item['name'] + (' ' + item.get('description', '') if not item.get('discovery') else '')
        return .1 if query.strip().casefold() in fallback.casefold() else 0
    complete = [(tier * 100 + len(words) / max(1, len(terms)))
                for tier, terms in fields if words <= terms]
    return max(complete, default=.5)


def ranked(packages, query=''):
    return sorted((p for p in packages if score(p, query)),
                  key=lambda p: (-score(p, query), p['name'].casefold(), p.get('repository', ''), p.get('version', '')))
