"""Builds map_master: every KBLI kelompok mapped to UNSPSC families by an LLM.

Writes into the SQLite database under data/. The reader (serve/app.py) only ever
opens that database read-only, so this script is the one thing that mutates it.
"""

import os
import json
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError


ROOT_DIR = Path(__file__).resolve().parent.parent

load_dotenv(ROOT_DIR / '.env')

# Relative SQLITE_PATH is read from the project root, so the run targets the same
# database whatever directory the script was launched from.
SQLITE_PATH = str(ROOT_DIR / os.getenv('SQLITE_PATH', 'data/database.sqlite'))

# Only KBLI at this DB level are mapped; ancestors are still pulled for context.
# DB level 4 = Kelompok (the 5-digit, most-granular KBLI level per definition.md).
KBLI_MAP_LEVEL = 4

# KBLI level -> Indonesian hierarchy name (see definition.md). Used to label the
# node and its ancestors in the context handed to the model.
KBLI_LEVEL_NAME = {
    0: 'Kategori',
    1: 'Golongan Pokok',
    2: 'Golongan',
    3: 'Sub Golongan',
    4: 'Kelompok',
}

# Cascade: three narrowing passes down the UNSPSC hierarchy — segment groups
# (level 0) -> segments (level 1) -> families (level 2). Each pass scopes the
# next pool to the parents just chosen; the final family list is the output.
# Shared framing prepended to every stage prompt.
SHARED_FRAMING = (
    'You bridge two taxonomies:\n'
    '- UNSPSC: a catalogue of concrete goods and services (commodities).\n'
    '- KBLI: the economic activity a business performs.\n'
    'You are given a set of UNSPSC selections (code, title, description) and a '
    'KBLI activity context (the activity plus its parent hierarchy up to the '
    'root). Your task is to identify which UNSPSC entries represent the direct '
    'OUTPUT of the activity — what it produces, sells, or delivers.\n'
    'Weigh goods and services equally:\n'
    '- If the activity makes or sells a physical product, match the commodity '
    'entries for that product.\n'
    '- If the activity performs a service, match the service entries for that '
    'service.\n'
    '- Some activities yield both; match both.\n'
    'A product may be catalogued under several entries covering its different '
    'stages or forms (e.g. raw material, semi-processed, finished, by-product); '
    'when the activity plausibly yields more than one such form, match each. '
    'Judge every case on the specific activity in front of you — do not assume a '
    'default sector. '
)

# Pass 1 (level 0) favours recall: a missed group drops every family beneath it.
SYSTEM_PROMPT_L0 = SHARED_FRAMING + (
    'This is a broad first pass over the top-level UNSPSC segment groups. '
    'Return every group that could plausibly contain the output of this activity '
    '— whether goods it produces or sells, or a service it provides. Favour '
    'recall: a group dropped here is lost for good, while later passes narrow '
    'what you keep. When unsure, include the group. '
    'Only return codes that appear in the given selections.'
)

# Pass 2 (level 1): recall-oriented — keep any segment that could hold the output
# in any of its forms; the final filter, not this pass, prunes.
SYSTEM_PROMPT_L1 = SHARED_FRAMING + (
    'This is the second pass over UNSPSC segments within the groups already '
    'chosen. Return every segment that could hold the activity\'s output in any '
    'of its forms or stages, plus every segment for a service the activity '
    'provides. Favour recall; a later pass filters. Drop only segments with no '
    'connection to the activity\'s output. '
    'Only return codes that appear in the given selections.'
)

# Pass 3 (level 2): recall-oriented — keep every family that holds the output in
# any form. The strict final filter, not this pass, does the pruning.
SYSTEM_PROMPT_L2 = SHARED_FRAMING + (
    'This is the family pass over UNSPSC level-2 families within the segments '
    'already chosen. Return every family that holds the activity\'s output in any '
    'of its forms or stages, plus every family for a service the activity '
    'provides. Favour recall; the next pass filters. Drop only families with no '
    'connection to the activity\'s output. '
    'Only return codes that appear in the given selections.'
)

# Final filter: re-examine the level-2 candidates alone and keep only the ones
# that strictly fit. A dedicated second look over the short candidate list
# catches families the broader family pass let through.
SYSTEM_PROMPT_FILTER = SHARED_FRAMING + (
    'You are given a short list of UNSPSC level-2 family candidates already '
    'matched to this activity. This is the precision pass: keep a family only if '
    'you can name the specific good or service the activity actually produces, '
    'sells, or delivers that belongs to it. When a product legitimately spans '
    'several families (different forms or stages of the same output), keep each '
    'of those families — do not collapse them to one. '
    'Drop candidates with no genuine connection: loosely related, adjacent, or '
    'speculative families, and generic families that do not match the activity\'s '
    'actual output. Do not pad — an empty list is correct when none genuinely '
    'fit. '
    'Only return codes that appear in the given selections.'
)

# Structured-output schema: the model must return {"codes": [...]}.
RESPONSE_FORMAT = {
    'type': 'json_schema',
    'json_schema': {
        'name': 'unspsc_codes',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'codes': {
                    'type': 'array',
                    'items': {'type': 'string'},
                },
            },
            'required': ['codes'],
            'additionalProperties': False,
        },
    },
}


class EmptyClassificationError(Exception):
    """Raised when a KBLI activity yields no coherent UNSPSC families."""


class RateLimitFatal(Exception):
    """Raised when a rate-limit retry also hit the limit — kills the run."""


class BudgetExceeded(Exception):
    """Raised when accumulated token cost reaches the configured budget — kills the run."""


# How long to wait after a rate-limit (429) before the single retry.
RATE_LIMIT_WAIT = 60


class BudgetTracker:
    """Accumulates USD spend from response token usage and enforces a cap.

    Prices are USD per 1,000,000 tokens (LLM_INPUT_PRICE / LLM_OUTPUT_PRICE).
    When ``budget`` is 0 the guard is disabled and nothing is tracked. Otherwise
    each response's usage is priced and added; once the running total reaches the
    budget, ``charge`` raises BudgetExceeded to abort the whole run.
    """

    def __init__(self, budget, input_price, output_price):
        self.budget = budget
        self.input_price = input_price
        self.output_price = output_price
        self.spent = 0.0

    @property
    def enabled(self):
        return self.budget > 0

    def charge(self, usage):
        """Price one response's usage, add it, and abort if over budget."""
        if not self.enabled or usage is None:
            return
        prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
        completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
        cost = (prompt_tokens * self.input_price +
                completion_tokens * self.output_price) / 1_000_000
        self.spent = self.spent + cost
        if self.spent >= self.budget:
            raise BudgetExceeded(
                'spent $' + format(self.spent, '.4f') +
                ' >= budget $' + format(self.budget, '.4f'))


def run():
    """Map every KBLI (all levels) to UNSPSC level-2 families.

    Loads the flat family pool once, walks every KBLI row, classifies it against
    the pool, and writes each (code_unspsc, code_kbli) pair into map_master.
    Already-mapped KBLI codes are skipped so the run is resumable.
    """
    # sqlite3 would happily create an empty file here and only fail later on a
    # missing table, so say plainly that the database itself is not there.
    if not os.path.exists(SQLITE_PATH):
        raise SystemExit(
            'Database not found at ' + SQLITE_PATH +
            ' — build it from data/structure.sql first.')

    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row

    client = OpenAI(
        base_url=os.getenv('LLM_BASE_URL', 'https://openrouter.ai/api/v1'),
        api_key=os.getenv('LLM_API_KEY'),
        timeout=60,
    )
    model = os.getenv('LLM_MODEL', 'deepseek/deepseek-v4-flash')

    budget = BudgetTracker(
        env_float('LLM_BUDGET_USD'),
        env_float('LLM_INPUT_PRICE'),
        env_float('LLM_OUTPUT_PRICE'),
    )

    try:
        groups = load_level(connection, 0)      # 10 top-level segment groups
        segments = load_level(connection, 1)    # 58 segments
        families = load_level(connection, 2)    # 559 level-2 families (output pool)

        done = load_mapped_kbli(connection)
        kbli_rows = load_kbli(connection)
        pending = [k for k in kbli_rows if k['code'] not in done]

        log('config', 'model=' + model + ' db=' + SQLITE_PATH)
        if budget.enabled:
            log('budget', 'cap $' + format(budget.budget, '.4f') +
                ' | in $' + format(budget.input_price, '.4f') +
                '/M out $' + format(budget.output_price, '.4f') + '/M')
        log('load', str(len(groups)) + ' groups / ' + str(len(segments)) +
            ' segments / ' + str(len(families)) + ' families | ' +
            str(len(kbli_rows)) + ' KBLI (' + str(len(done)) + ' already mapped, ' +
            str(len(pending)) + ' pending)')

        total = len(pending)
        mapped = 0
        errors = 0
        started = time.time()
        for i, kbli in enumerate(pending, start=1):
            head = '[' + str(i) + '/' + str(total) + '] ' + kbli['code'] + \
                ' (' + KBLI_LEVEL_NAME.get(kbli['level'], str(kbli['level'])) + ') ' + \
                (kbli['title'] or '')
            log('map', head)

            context = build_context(connection, kbli)

            try:
                codes = classify_cascade(client, model, groups, segments, families, context, budget)
                write_mapping(connection, kbli['code'], codes)
                connection.commit()
                mapped = mapped + 1
                log('  ok', str(len(codes)) + ' families -> ' + ', '.join(codes))
            except BudgetExceeded as e:
                # Spend hit the cap mid-KBLI — stop cleanly (run is resumable).
                log('fatal', 'budget exceeded, aborting: ' + str(e))
                break
            except RateLimitFatal as e:
                # Rate limit persisted through the retry — stop the whole run.
                log('fatal', 'rate limit persisted, aborting: ' + repr(e))
                raise
            except Exception as e:
                errors = errors + 1
                log(' err', repr(e))

            time.sleep(1)

        elapsed = round(time.time() - started)
        spend = ', spent $' + format(budget.spent, '.4f') if budget.enabled else ''
        log('done', 'mapped ' + str(mapped) + '/' + str(total) +
            ', ' + str(errors) + ' errors, ' + str(elapsed) + 's' + spend)
    finally:
        connection.close()


def env_float(name):
    """Read an env var as a float; blank/unset/garbage -> 0.0 (guard disabled)."""
    try:
        return float(os.getenv(name, '') or 0)
    except ValueError:
        return 0.0


def log(tag, message):
    """Timestamped console line, flushed so progress streams live."""
    stamp = time.strftime('%H:%M:%S')
    print(stamp + ' ' + tag.rjust(6) + ' | ' + message, flush=True)


def load_level(connection, level):
    """Load one UNSPSC level as {code: {code, title, description, parent_code}}."""
    pool = {}
    rows = connection.execute(
        'SELECT code, title, description, definition, parent_code '
        'FROM master_unspsc WHERE level = ? ORDER BY code ASC',
        [level],
    )
    for row in rows:
        if row['code'] is None or row['title'] is None:
            continue
        pool[row['code']] = {
            'code': row['code'],
            'title': row['title'],
            # descriptions are sparse at some levels; fall back to the longer
            # definition so the model still gets a gloss when one exists.
            'description': row['description'] or row['definition'],
            'parent_code': row['parent_code'],
        }
    return pool


def scope_to_parents(pool, parent_codes):
    """Subset of ``pool`` whose parent_code is in ``parent_codes`` (order kept)."""
    chosen = set(parent_codes)
    return {code: row for code, row in pool.items() if row['parent_code'] in chosen}


def load_kbli(connection):
    """Load the mappable KBLI rows (Kelompok / KBLI_MAP_LEVEL), sorted by code."""
    rows = connection.execute(
        'SELECT code, title, description, definition, parent_code, level '
        'FROM master_kbli WHERE level = ? ORDER BY code',
        [KBLI_MAP_LEVEL],
    )
    return [dict(row) for row in rows if row['code'] is not None]


def load_mapped_kbli(connection):
    """Return the set of KBLI codes already present in map_master (resume)."""
    return {row['code_kbli'] for row in connection.execute('SELECT DISTINCT code_kbli FROM map_master')}


def kbli_description(row):
    """Gloss for a KBLI row: prefer the fuller definition, else description.

    Many definitions are bare cross-references ("Lihat subgolongan 9900.") that
    carry no descriptive signal — for those, fall back to the description.
    """
    definition = (row['definition'] or '').strip()
    if definition and not definition.lower().startswith('lihat'):
        return definition
    return row['description'] or (definition or None)


def build_context(connection, kbli):
    """Serialise the KBLI activity plus its ancestor chain up to the root.

    The node and each ancestor are emitted as {Code, Level, Description}, ordered
    root-first so the model reads the narrowing hierarchy top to bottom.
    """
    chain = []
    node = kbli
    seen = set()
    while node is not None and node['code'] not in seen:
        seen.add(node['code'])
        chain.append({
            'Code': node['code'],
            'Level': KBLI_LEVEL_NAME.get(node['level'], node['level']),
            'Title': node['title'],
            'Description': kbli_description(node),
        })
        parent_code = node['parent_code']
        if parent_code is None:
            break
        parent = connection.execute(
            'SELECT code, title, description, definition, parent_code, level '
            'FROM master_kbli WHERE code = ?',
            [parent_code],
        ).fetchone()
        node = dict(parent) if parent is not None else None

    chain.reverse()
    return json.dumps({'activity': chain[-1], 'hierarchy': chain}, ensure_ascii=False)


def selection_line(selection):
    """One family rendered as 'code, title — description' for the prompt."""
    line = selection['code'] + ', ' + selection['title']
    if selection.get('description'):
        line = line + ' — ' + selection['description']
    return line


def render_groups(groups, segments):
    """Render L0 groups, each annotated with the titles of its child segments.

    Group titles alone are too coarse (and carry no description) to place a
    commodity; listing the segments beneath each group gives the model enough
    signal to pick, e.g., the Food group for a corn-farming activity.
    """
    children = {}
    for segment in segments.values():
        children.setdefault(segment['parent_code'], []).append(segment['title'])

    lines = []
    for code, group in groups.items():
        line = code + ', ' + group['title']
        titles = children.get(code)
        if titles:
            line = line + ' — contains: ' + '; '.join(titles)
        lines.append(line)
    return '\n'.join(lines)


def write_mapping(connection, code_kbli, codes):
    """Insert one (code_unspsc, code_kbli) row per matched family."""
    connection.executemany(
        'INSERT INTO map_master (code_unspsc, code_kbli) VALUES (?, ?)',
        [(code, code_kbli) for code in codes],
    )


def classify_cascade(client, model, groups, segments, families, context, budget):
    """Three narrowing passes: group (L0) -> segment (L1) -> family (L2).

    Each pass scopes the next pool to the parents just chosen. An empty result at
    any stage collapses the cascade (nothing survives downstream), surfacing as
    an EmptyClassificationError for the KBLI. Returns the level-2 family codes.
    """
    # L0 group titles are coarse and description-less, so enrich each group line
    # with the titles of the segments beneath it — that is the only signal the
    # model has to tell (e.g.) the Food group from the Raw-Materials group.
    group_block = render_groups(groups, segments)
    group_codes = classify(client, model, SYSTEM_PROMPT_L0, groups, context, budget, group_block)
    log('   l0', str(len(group_codes)) + ' groups -> ' + ', '.join(group_codes))

    segment_pool = scope_to_parents(segments, group_codes)
    segment_codes = classify(client, model, SYSTEM_PROMPT_L1, segment_pool, context, budget)
    log('   l1', str(len(segment_codes)) + ' segments -> ' + ', '.join(segment_codes))

    family_pool = scope_to_parents(families, segment_codes)
    family_codes = classify(client, model, SYSTEM_PROMPT_L2, family_pool, context, budget)
    log('   l2', str(len(family_codes)) + ' candidates -> ' + ', '.join(family_codes))

    # Final filter: strict second look over just the candidates.
    candidates = {code: families[code] for code in family_codes}
    filtered = classify(client, model, SYSTEM_PROMPT_FILTER, candidates, context, budget)
    return filtered


def classify(client, model, system_prompt, selections, context, budget, selection_block=None):
    """Run one classification pass over ``selections`` for the KBLI ``context``.

    ``selections`` is the {code: row} pool the model must choose from; its output
    is validated back against those codes. ``selection_block`` optionally
    overrides how the pool is rendered (e.g. groups annotated with their child
    segments); when omitted each selection is one ``selection_line``. Raises
    EmptyClassificationError when the pool is empty or nothing valid comes back.
    """
    if len(selections) == 0:
        raise EmptyClassificationError()

    if selection_block is None:
        selection_block = '\n'.join(selection_line(s) for s in selections.values())
    user_prompt = (
        'With these UNSPSC selections:\n' + selection_block +
        '\n\nAnd this KBLI activity context:\n' + context +
        '\n\nGive only the codes that fit, from the selections above. '
        'Classify accurately; return an empty list if none genuinely fit.'
    )

    extra_body = {}
    providers = [p.strip() for p in os.getenv('LLM_PROVIDER', '').split(',') if p.strip()]
    if providers:
        extra_body['provider'] = {'only': providers}

    # Generic transient errors get a short exponential backoff (attempt budget).
    # Rate-limit (429) is handled apart: one fixed wait, then a second 429 is
    # fatal to the whole run — so it never consumes the generic budget.
    max_attempts = 3
    attempt = 0
    rate_limit_retried = False
    while True:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
                response_format=RESPONSE_FORMAT,
                temperature=0.375,
                extra_body=extra_body,
            )

            # Price this call; raises BudgetExceeded if the cap is now reached.
            budget.charge(getattr(response, 'usage', None))

            payload = json.loads(response.choices[0].message.content)
            codes = [str(code).strip() for code in payload.get('codes', [])]

            valid = [code for code in codes if code in selections]
            if len(valid) == 0:
                raise EmptyClassificationError()

            # dedupe, preserve order
            return list(dict.fromkeys(valid))
        except RateLimitError as e:
            # One retry after a fixed wait; a second 429 kills the whole run.
            if rate_limit_retried:
                raise RateLimitFatal(repr(e))
            rate_limit_retried = True
            log(' rate', 'limit hit, retry in ' + str(RATE_LIMIT_WAIT) + 's')
            time.sleep(RATE_LIMIT_WAIT)
        except BudgetExceeded:
            # Budget cap is terminal — never retry, let it abort the run.
            raise
        except Exception:
            attempt = attempt + 1
            if attempt >= max_attempts:
                raise
            time.sleep(2 ** (attempt - 1))


if __name__ == '__main__':
    run()
