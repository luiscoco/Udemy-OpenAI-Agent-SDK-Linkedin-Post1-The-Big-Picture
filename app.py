"""Lesson 1: from a user goal to a completed, verified task.

Goal-to-Result Task Runner. One small CLI walks through the whole Lesson 1 flow:
  1A  turn a goal into a checkable task contract   -> TaskSpec, `task` command
  1B  configure reusable agent behavior            -> AgentConfig
  1C  start a session and keep its ID              -> SessionStore, `run`, `status`
  1D  choose the execution environment             -> tools_for('none' | 'hosted')
  1E  observe progress with events                 -> streamed events + JSONL event log
  1F  review the result and continue the session   -> verify_result, `history`, `follow-up`
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable
from uuid import uuid4

HERE = Path(__file__).resolve().parent
TWO_PLACES = Decimal('0.01')

# ---------------------------------------------------------------------------
# 1B  Reusable agent behavior
# ---------------------------------------------------------------------------
INSTRUCTIONS = (
    'You are a careful sales analyst. Compare the supplied sales figures and show the calculation. '
    'Use only values supplied in this conversation. Do not invent, estimate, or look up data. '
    'Return the requested JSON object: higher_region and lower_region are region names exactly as '
    'supplied (use "Equal" for both when the values are equal); difference is the absolute difference '
    'as a decimal string; percentage_gap is null unless the user asks for it, and then it is '
    'difference / lower value * 100, rounded to two decimals, as a string without a percent sign; '
    'summary is one or two sentences that state which region is higher, by how much, and the calculation.'
)

RESULT_FORMAT = {
    'type': 'json_schema', 'name': 'sales_comparison', 'strict': True,
    'schema': {
        'type': 'object',
        'properties': {
            'higher_region': {'type': 'string'},
            'lower_region': {'type': 'string'},
            'difference': {'type': 'string'},
            'percentage_gap': {'type': ['string', 'null']},
            'summary': {'type': 'string'},
        },
        'required': ['higher_region', 'lower_region', 'difference', 'percentage_gap', 'summary'],
        'additionalProperties': False,
    },
}

FOLLOW_UP_PROMPT = (
    'Also show the percentage gap. Use the regional sales figures from earlier in this conversation. '
    'Percentage gap = difference / lower value * 100, rounded to two decimals.'
)


@dataclass(frozen=True)
class AgentConfig:
    """Stable behavior reused by every session (an application-level configuration)."""
    model: str
    instructions: str = INSTRUCTIONS


# ---------------------------------------------------------------------------
# 1A  Task contract: goal, input data, deliverable, checks, boundaries
# ---------------------------------------------------------------------------
def amount(value: Any) -> Decimal:
    """Parse one sales figure: finite, 0..1e9, at most two decimal places."""
    if isinstance(value, bool):
        raise ValueError('Sales amounts must be numbers.')
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError(f'Sales amount {value!r} is not a decimal number.') from None
    if not result.is_finite() or result < 0 or result > Decimal('1000000000'):
        raise ValueError('Sales must be finite numbers between 0 and 1,000,000,000.')
    if result != result.quantize(TWO_PLACES):
        raise ValueError('Use at most two decimal places for sales.')
    return result


@dataclass(frozen=True)
class TaskSpec:
    goal: str
    data: tuple[tuple[str, Decimal], tuple[str, Decimal]]
    deliverable: str
    checks: tuple[str, ...]
    boundaries: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'TaskSpec':
        if not isinstance(raw, dict):
            raise ValueError('The task file must contain a JSON object.')
        missing = [k for k in ('goal', 'data', 'deliverable', 'checks', 'boundaries') if k not in raw]
        if missing:
            raise ValueError(f'The task is missing: {", ".join(missing)}.')
        data = raw['data']
        if not isinstance(data, dict) or len(data) != 2:
            raise ValueError('"data" must contain exactly two regions, e.g. {"North": 120, "South": 90}.')
        regions = []
        for name, value in data.items():
            if not re.fullmatch(r'[A-Za-z][A-Za-z ]{0,29}', name) or name.strip().lower() == 'equal':
                raise ValueError(f'Region name {name!r} must be 1-30 letters/spaces and not "Equal".')
            regions.append((name, amount(value)))
        if regions[0][0].lower() == regions[1][0].lower():
            raise ValueError('The two regions need different names.')
        def texts(key: str) -> tuple[str, ...]:
            items = raw[key]
            if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
                raise ValueError(f'"{key}" must be a list of non-empty strings.')
            return tuple(i.strip() for i in items)
        for key in ('goal', 'deliverable'):
            if not isinstance(raw[key], str) or not raw[key].strip():
                raise ValueError(f'"{key}" must be a non-empty string.')
        return cls(raw['goal'].strip(), (regions[0], regions[1]), raw['deliverable'].strip(),
                   texts('checks'), texts('boundaries'))

    @classmethod
    def from_file(cls, path: Path) -> 'TaskSpec':
        try:
            raw = json.loads(Path(path).read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError(f'Task file not found: {path}') from None
        except json.JSONDecodeError as error:
            raise ValueError(f'Task file is not valid JSON: {error}') from None
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {'goal': self.goal, 'data': {n: str(v) for n, v in self.data},
                'deliverable': self.deliverable, 'checks': list(self.checks),
                'boundaries': list(self.boundaries)}

    def prompt(self) -> str:
        """The first user message: the whole contract, so the agent knows what success means."""
        lines = [f'Goal: {self.goal}',
                 'Data (use only these values): ' + json.dumps({n: str(v) for n, v in self.data}),
                 f'Deliverable: {self.deliverable}', 'Success checks:']
        lines += [f'- {c}' for c in self.checks]
        lines.append('Boundaries:')
        lines += [f'- {b}' for b in self.boundaries]
        return '\n'.join(lines)


# ---------------------------------------------------------------------------
# 1F  Independent verification of the result
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def expected_values(spec: TaskSpec) -> dict[str, Any]:
    (name_a, a), (name_b, b) = spec.data
    difference = abs(a - b)
    if a == b:
        higher, lower = 'Equal', 'Equal'
    else:
        higher, lower = (name_a, name_b) if a > b else (name_b, name_a)
    low_value = min(a, b)
    percentage = None if low_value == 0 else (difference / low_value * 100).quantize(TWO_PLACES, ROUND_HALF_UP)
    return {'higher': higher, 'lower': lower, 'difference': difference, 'percentage': percentage}


NUMBER = re.compile(r'(?<![\w.])-?\d[\d,]*(?:\.\d+)?')


def numbers_in(text: str) -> list[Decimal]:
    found = []
    for match in NUMBER.findall(text):
        try:
            found.append(Decimal(match.replace(',', '')))
        except InvalidOperation:
            pass
    return found


def as_decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).strip().rstrip('%'))
    except (InvalidOperation, AttributeError):
        return None
    return number if number.is_finite() else None


def verify_result(result: Any, spec: TaskSpec, want_percentage: bool = False) -> list[Check]:
    """Apply the task's success checks without trusting the model's own claims."""
    if not isinstance(result, dict) or not all(isinstance(result.get(k), str)
                                               for k in ('higher_region', 'lower_region', 'difference', 'summary')):
        return [Check('result_shape', False, 'The result does not have the expected fields.')]
    exp = expected_values(spec)
    checks = []

    difference = as_decimal(result['difference'])
    checks.append(Check('correct_arithmetic', difference == exp['difference'],
                        f'expected {exp["difference"]}, received {result["difference"]!r}'))

    same = (result['higher_region'].strip().lower() == exp['higher'].lower()
            and result['lower_region'].strip().lower() == exp['lower'].lower())
    checks.append(Check('clear_comparison', same,
                        f'expected higher={exp["higher"]}, lower={exp["lower"]}; '
                        f'received higher={result["higher_region"]!r}, lower={result["lower_region"]!r}'))

    values = [v for _, v in spec.data]
    allowed = set(values) | {exp['difference'], sum(values), Decimal(100)}
    if exp['percentage'] is not None:
        allowed |= {exp['percentage'].quantize(Decimal(q), ROUND_HALF_UP) for q in ('1', '0.1', '0.01')}
    # Compare absolute values: "120-90" must not be read as an invented "-90".
    invented = [str(n) for n in numbers_in(result['summary']) if abs(n) not in allowed]
    checks.append(Check('no_invented_values', not invented,
                        'only supplied or derived numbers' if not invented
                        else f'unexpected numbers in summary: {", ".join(invented)}'))

    if want_percentage:
        received = result.get('percentage_gap')
        if exp['percentage'] is None:
            ok = received is None
            detail = f'lower value is 0, expected null; received {received!r}'
        else:
            pct = as_decimal(received)
            ok = pct is not None and abs(pct - exp['percentage']) <= TWO_PLACES
            detail = f'expected {exp["percentage"]}, received {received!r}'
        checks.append(Check('percentage_gap', ok, detail))
    return checks


# ---------------------------------------------------------------------------
# 1D  Execution environment
# ---------------------------------------------------------------------------
def tools_for(environment: str) -> list[dict[str, Any]]:
    if environment == 'none':
        return []
    if environment == 'hosted':
        return [{'type': 'code_interpreter', 'container': {'type': 'auto'}}]
    raise ValueError('Environment must be none or hosted.')


# ---------------------------------------------------------------------------
# 1C  Session registry: a local name -> conversation ID (+ the turn record)
# ---------------------------------------------------------------------------
class SessionStore:
    def __init__(self, root: Path, backend: str):
        self.root = Path(root) / backend
        self.root.mkdir(parents=True, exist_ok=True)
        self.backend = backend

    def path(self, name: str) -> Path:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}', name):
            raise ValueError('Session names need 1-48 letters, digits, underscores, or hyphens.')
        return self.root / f'{name}.json'

    def exists(self, name: str) -> bool:
        return self.path(name).exists()

    def load(self, name: str) -> dict[str, Any]:
        path = self.path(name)
        if not path.exists():
            raise ValueError(f'No {self.backend} session named {name!r}. Start it with run.')
        return json.loads(path.read_text(encoding='utf-8'))

    def save(self, session: dict[str, Any]) -> None:
        path = self.path(session['name'])
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(session, indent=2), encoding='utf-8')
        temporary.replace(path)


# ---------------------------------------------------------------------------
# 1E  Turn outcomes
# ---------------------------------------------------------------------------
class TurnFailed(Exception):
    """The turn ended with an error or an incomplete response."""


class TurnCancelled(Exception):
    """The turn was stopped before it completed (for example with Ctrl+C)."""


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class DemoBackend:
    """Deterministic offline simulation. No model, no network, no sandbox execution."""

    def __init__(self, simulate: str = 'completed'):
        self.simulate = simulate

    def create(self) -> str:
        return 'demo_conv_' + uuid4().hex[:16]

    def retrieve(self, session: dict[str, Any]) -> dict[str, Any]:
        return {'id': session['conversation_id'], 'object': 'demo.conversation',
                'saved_items': len(session.get('items', []))}

    def turn(self, session: dict[str, Any], prompt: str, kind: str,
             emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        spec = TaskSpec.from_dict(session['task'])
        emit({'type': 'demo.response.created'})
        session['items'].append({'type': 'message', 'role': 'user', 'content': prompt})
        emit({'type': 'demo.response.in_progress'})
        if session['environment'] == 'hosted':
            emit({'type': 'demo.code_interpreter_call.simulated',
                  'note': 'Illustration only: no hosted container or code execution.'})
            session['items'].append({'type': 'simulated_tool_call', 'name': 'code_interpreter',
                                     'note': 'Demo only; not executed.'})
        if self.simulate == 'failed':
            emit({'type': 'demo.response.failed', 'error': 'Simulated tool error.'})
            raise TurnFailed('Simulated failure: the turn ended with an error.')
        exp = expected_values(spec)
        (name_a, a), (name_b, b) = spec.data
        if exp['higher'] == 'Equal':
            summary = f'{name_a} and {name_b} are equal ({a} vs {b}); the difference is 0.'
        else:
            hi, lo = (a, b) if exp['higher'] == name_a else (b, a)
            summary = (f'{exp["higher"]} sales are {exp["difference"]} higher than {exp["lower"]} '
                       f'({hi} - {lo} = {exp["difference"]}).')
        percentage = None
        if kind == 'follow-up':
            percentage = None if exp['percentage'] is None else str(exp['percentage'])
            if percentage is not None:
                summary += f' Percentage gap: {exp["difference"]} / {min(a, b)} * 100 = {percentage}%.'
        result = {'higher_region': exp['higher'], 'lower_region': exp['lower'],
                  'difference': str(exp['difference']), 'percentage_gap': percentage, 'summary': summary}
        text = json.dumps(result)
        for start in range(0, len(text), 40):
            emit({'type': 'demo.output_text.delta', 'delta': text[start:start + 40]})
            if self.simulate == 'cancelled' and start >= 40:
                emit({'type': 'demo.response.cancelled'})
                raise TurnCancelled('Simulated cancellation: the turn stopped before completing.')
        session['items'].append({'type': 'message', 'role': 'assistant', 'content': result})
        emit({'type': 'demo.response.completed'})
        return result

    def history(self, session: dict[str, Any]):
        yield from session['items']


class LiveBackend:
    """Public OpenAI API: Conversations (session state) + Responses (each turn)."""

    def __init__(self, config: AgentConfig, client: Any):
        self.config, self.client = config, client

    def create(self) -> str:
        return self.client.conversations.create(metadata={'lesson': '1'}).id

    def retrieve(self, session: dict[str, Any]) -> dict[str, Any]:
        return self.client.conversations.retrieve(session['conversation_id']).model_dump(mode='json')

    def turn(self, session: dict[str, Any], prompt: str, kind: str,
             emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        tools = tools_for(session['environment'])
        instructions = self.config.instructions
        if tools:
            instructions += ' Use the python tool to perform the arithmetic.'
        stream = self.client.responses.create(
            model=self.config.model, instructions=instructions,
            conversation=session['conversation_id'], input=prompt,
            tools=tools, tool_choice='required' if tools else 'auto',
            text={'format': RESULT_FORMAT}, stream=True,
        )
        final = None
        try:
            for event in stream:
                emit(event.model_dump(mode='json'))
                if event.type == 'response.completed':
                    final = event.response
                elif event.type == 'response.failed':
                    raise TurnFailed('The turn ended with response.failed; inspect the event log.')
                elif event.type == 'response.incomplete':
                    raise TurnFailed('The response is incomplete; inspect the event log.')
                elif event.type == 'error':
                    raise TurnFailed('The stream reported an error; inspect the event log.')
        except KeyboardInterrupt:
            raise TurnCancelled('Stopped listening (Ctrl+C). Remote work may already have happened.') from None
        finally:
            stream.close()
        if final is None or final.status != 'completed':
            raise TurnFailed('The stream ended without a completed response.')
        if not final.output_text:
            raise TurnFailed('The completed response has no output text; check history for a refusal.')
        try:
            return json.loads(final.output_text)
        except json.JSONDecodeError:
            raise TurnFailed('The output is not the requested JSON object.') from None

    def history(self, session: dict[str, Any]):
        # Iterating the SDK page follows later pages automatically.
        for item in self.client.conversations.items.list(
            conversation_id=session['conversation_id'], order='asc', limit=100,
        ):
            yield item.model_dump(mode='json')


# ---------------------------------------------------------------------------
# Running one turn: record -> stream events -> verify -> save outcome
# ---------------------------------------------------------------------------
def execute_turn(store: SessionStore, backend: Any, session: dict[str, Any],
                 prompt: str, kind: str) -> tuple[dict[str, Any], list[Check]]:
    number = len(session['turns']) + 1
    log_path = store.root / f'{session["name"]}-turn-{number}.events.jsonl'
    # Record the attempt BEFORE sending it, so an interrupted turn stays visible.
    session['turns'].append({'number': number, 'kind': kind, 'attempt_id': uuid4().hex,
                             'status': 'running', 'events': log_path.name})
    store.save(session)
    print(f'Session {session["name"]} -> conversation {session["conversation_id"]} '
          f'| environment: {session["environment"]} | turn {number} ({kind})')
    turn = session['turns'][-1]
    mid_line = False  # True while streamed text is being printed on the current line
    with log_path.open('w', encoding='utf-8') as log:
        def emit(event: dict[str, Any]) -> None:
            nonlocal mid_line
            log.write(json.dumps(event) + '\n')
            log.flush()
            # Observe only: printing an event never re-runs a tool.
            event_type = event.get('type', '')
            if event_type.endswith('output_text.delta'):
                print(event.get('delta', ''), end='', flush=True)
                mid_line = True
            elif not event_type.endswith('.delta'):
                print(('\n' if mid_line else '') + f'[event] {event_type}', flush=True)
                mid_line = False
        try:
            result = backend.turn(session, prompt, kind, emit)
        except TurnCancelled:
            turn['status'] = 'cancelled'
            raise
        except Exception:
            turn['status'] = 'failed'
            raise
        finally:
            store.save(session)
    spec = TaskSpec.from_dict(session['task'])
    checks = verify_result(result, spec, want_percentage=(kind == 'follow-up'))
    turn['checks'] = {c.name: c.passed for c in checks}
    turn['status'] = 'completed_and_verified' if all(c.passed for c in checks) else 'completed_checks_failed'
    store.save(session)
    print('\n[RESULT]')
    for key in ('higher_region', 'lower_region', 'difference', 'percentage_gap', 'summary'):
        if isinstance(result, dict) and key in result:
            print(f'  {key:15} {result[key]}')
    print('[CHECKS]')
    for check in checks:
        print(f'  [{"PASS" if check.passed else "FAIL"}] {check.name}: {check.detail}')
    print(f'[OUTCOME] {turn["status"]}')
    print(f'Event log: {log_path}')
    return result, checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Lesson 1: Goal-to-Result Task Runner.')
    p.add_argument('--backend', choices=['demo', 'live'], default='demo')
    p.add_argument('--state-dir', type=Path, default=HERE / '.lesson_state')
    commands = p.add_subparsers(dest='command', required=True)

    show = commands.add_parser('task', help='1A: show the task contract and the prompt (no API call).')
    show.add_argument('--task', type=Path, default=HERE / 'task.json')

    run = commands.add_parser('run', help='1C: start a new session with the task as its first turn.')
    run.add_argument('--session', required=True)
    run.add_argument('--task', type=Path, default=HERE / 'task.json')
    run.add_argument('--environment', choices=['none', 'hosted'], default='none')
    run.add_argument('--simulate', choices=['completed', 'failed', 'cancelled'], default='completed',
                     help='Demo backend only: choose the turn outcome to observe.')

    status = commands.add_parser('status', help='1C: show the saved session state.')
    status.add_argument('--session', required=True)

    follow = commands.add_parser('follow-up', help='1F: ask for the percentage gap in the same session.')
    follow.add_argument('--session', required=True)
    follow.add_argument('--retry', action='store_true',
                        help='Send again although the previous attempt was interrupted.')
    follow.add_argument('--simulate', choices=['completed', 'failed', 'cancelled'], default='completed')

    history = commands.add_parser('history', help='1F: list the saved conversation items.')
    history.add_argument('--session', required=True)
    return p


def make_backend(args: argparse.Namespace, session: dict[str, Any] | None) -> tuple[Any, str]:
    if args.backend == 'demo':
        print('[DEMO] Simulated events and answers. No API calls or sandbox execution.')
        return DemoBackend(getattr(args, 'simulate', 'completed')), 'offline-simulation'
    if getattr(args, 'simulate', 'completed') != 'completed':
        raise ValueError('--simulate is only available with the demo backend.')
    try:
        from dotenv import load_dotenv
        from openai import OpenAI
    except ImportError:
        raise ValueError('Install live dependencies: python -m pip install -r requirements.txt') from None
    load_dotenv(HERE / '.env')
    if not os.environ.get('OPENAI_API_KEY') or not os.environ.get('OPENAI_MODEL'):
        raise ValueError('Set OPENAI_API_KEY and OPENAI_MODEL in .env or your environment.')
    # An existing session keeps the model it started with.
    model = session['model'] if session else os.environ['OPENAI_MODEL']
    return LiveBackend(AgentConfig(model), OpenAI(timeout=120.0, max_retries=0)), model


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == 'task':
            spec = TaskSpec.from_file(args.task)
            print('[TASK CONTRACT]')
            print(json.dumps(spec.to_dict(), indent=2))
            print('\n[PROMPT SENT AS THE FIRST TURN]')
            print(spec.prompt())
            exp = expected_values(spec)
            print('\n[WHAT THE CHECKS WILL EXPECT]')
            print(f'  higher={exp["higher"]} lower={exp["lower"]} difference={exp["difference"]} '
                  f'percentage_gap={exp["percentage"]}')
            return 0

        store = SessionStore(args.state_dir, args.backend)
        if args.command == 'run':
            if store.exists(args.session):
                raise ValueError('That session already exists. Use follow-up or choose a new name.')
            spec = TaskSpec.from_file(args.task)
            backend, model = make_backend(args, None)
            session = dict(name=args.session, conversation_id=backend.create(), model=model,
                           environment=args.environment, task=spec.to_dict(), items=[], turns=[])
            store.save(session)
            _, checks = execute_turn(store, backend, session, spec.prompt(), 'initial')
            return 0 if all(c.passed for c in checks) else 2

        session = store.load(args.session)
        backend, _ = make_backend(args, session)
        if args.command == 'status':
            turns = session['turns']
            print(f'Session:      {session["name"]}')
            print(f'Conversation: {session["conversation_id"]}')
            print(f'Model:        {session["model"]}   Environment: {session["environment"]}')
            print(f'Remote state: {json.dumps(backend.retrieve(session))}')
            print('Turns:')
            for turn in turns:
                print(f'  #{turn["number"]} {turn["kind"]:9} {turn["status"]}')
            if turns and turns[-1]['status'] == 'running':
                print('Note: the last turn never reported an outcome. A session existing is not proof '
                      'that its turn finished.')
            return 0
        if args.command == 'follow-up':
            if not any(t['kind'] == 'initial' and t['status'] == 'completed_and_verified'
                       for t in session['turns']):
                raise ValueError('This session has no verified initial turn. Start a new session.')
            if session['turns'][-1]['status'] == 'running' and not args.retry:
                raise ValueError('The previous attempt was interrupted and may have reached the server. '
                                 'Check `history` first, then use --retry to send again.')
            _, checks = execute_turn(store, backend, session, FOLLOW_UP_PROMPT, 'follow-up')
            return 0 if all(c.passed for c in checks) else 2
        print(f'Saved items for {session["conversation_id"]} (oldest first):')
        for item in backend.history(session):
            print(json.dumps(item, indent=2))
        return 0
    except TurnCancelled as error:
        print(f'\nCancelled: {error}', file=sys.stderr)
        return 130
    except Exception as error:
        # Short messages only: no tracebacks or credential dumps in a student CLI.
        print(f'\nError: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
