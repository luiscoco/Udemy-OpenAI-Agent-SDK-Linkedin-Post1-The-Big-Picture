"""Offline tests: no network, no API key."""
import contextlib
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import (AgentConfig, DemoBackend, LiveBackend, SessionStore, TaskSpec, TurnCancelled,
                 TurnFailed, amount, execute_turn, expected_values, main, numbers_in, tools_for,
                 verify_result)

TASK = {'goal': 'Compare regional sales.', 'data': {'North': 120, 'South': 90},
        'deliverable': 'A concise comparison.', 'checks': ['Use only the supplied values.'],
        'boundaries': ['Do not guess.']}
GOOD = {'higher_region': 'North', 'lower_region': 'South', 'difference': '30',
        'percentage_gap': None, 'summary': 'North is 30 higher than South (120-90 = 30).'}


def quiet(fn, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = fn(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


class Event:
    def __init__(self, kind, response=None):
        self.type, self.response = kind, response

    def model_dump(self, **kwargs):
        return {'type': self.type}


class Stream:
    def __init__(self, events, interrupt=False):
        self.events, self.closed, self.interrupt = events, False, interrupt

    def __iter__(self):
        yield from self.events
        if self.interrupt:
            raise KeyboardInterrupt

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, stream):
        self.stream, self.requests = stream, []
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.stream


def completed(text):
    return Event('response.completed', SimpleNamespace(id='resp_1', status='completed', output_text=text))


class TaskContractTests(unittest.TestCase):
    def test_prompt_contains_every_part_of_the_contract(self):
        prompt = TaskSpec.from_dict(TASK).prompt()
        for text in ('Goal:', '"North": "120"', 'Deliverable:', 'Success checks:', 'Boundaries:'):
            self.assertIn(text, prompt)

    def test_rejects_bad_tasks(self):
        for change in ({'data': {'North': 1}}, {'data': {'North': 1, 'north': 2}},
                       {'data': {'North': 'x', 'South': 1}}, {'data': {'Equal': 1, 'South': 1}},
                       {'checks': 'not a list'}, {'goal': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                TaskSpec.from_dict({**TASK, **change})

    def test_amount_limits(self):
        self.assertEqual(amount('125.50'), Decimal('125.50'))
        for bad in ('-1', '1.234', 'nan', 'inf', '2000000000', True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                amount(bad)

    def test_expected_values(self):
        exp = expected_values(TaskSpec.from_dict(TASK))
        self.assertEqual((exp['higher'], exp['difference'], exp['percentage']),
                         ('North', Decimal(30), Decimal('33.33')))
        equal = expected_values(TaskSpec.from_dict({**TASK, 'data': {'North': 5, 'South': 5}}))
        self.assertEqual((equal['higher'], equal['lower']), ('Equal', 'Equal'))
        zero = expected_values(TaskSpec.from_dict({**TASK, 'data': {'North': 5, 'South': 0}}))
        self.assertIsNone(zero['percentage'])


class VerificationTests(unittest.TestCase):
    spec = TaskSpec.from_dict(TASK)

    def names(self, checks):
        return {c.name: c.passed for c in checks}

    def test_good_result_passes(self):
        self.assertTrue(all(self.names(verify_result(GOOD, self.spec)).values()))

    def test_wrong_arithmetic_fails(self):
        result = self.names(verify_result({**GOOD, 'difference': '40'}, self.spec))
        self.assertFalse(result['correct_arithmetic'])

    def test_swapped_regions_fail(self):
        result = self.names(verify_result({**GOOD, 'higher_region': 'South', 'lower_region': 'North'}, self.spec))
        self.assertFalse(result['clear_comparison'])

    def test_invented_number_fails(self):
        result = self.names(verify_result({**GOOD, 'summary': 'North is 30 higher; last year it was 75.'}, self.spec))
        self.assertFalse(result['no_invented_values'])

    def test_number_parsing(self):
        self.assertEqual(numbers_in('1,200.50 and 120-90 = 30, change -5'),
                         [Decimal('1200.50'), Decimal(120), Decimal(90), Decimal(30), Decimal(-5)])

    def test_percentage_gap(self):
        good = {**GOOD, 'percentage_gap': '33.33', 'summary': 'Gap 30 / 90 * 100 = 33.3%.'}
        self.assertTrue(all(self.names(verify_result(good, self.spec, want_percentage=True)).values()))
        bad = self.names(verify_result({**GOOD, 'percentage_gap': '25'}, self.spec, want_percentage=True))
        self.assertFalse(bad['percentage_gap'])

    def test_wrong_shape(self):
        self.assertEqual(self.names(verify_result(['x'], self.spec)), {'result_shape': False})


class EnvironmentTests(unittest.TestCase):
    def test_tools(self):
        self.assertEqual(tools_for('none'), [])
        self.assertEqual(tools_for('hosted')[0]['type'], 'code_interpreter')
        with self.assertRaises(ValueError):
            tools_for('shell')


class LiveBackendTests(unittest.TestCase):
    session = {'conversation_id': 'conv_1', 'environment': 'none'}

    def test_request_shape_and_result(self):
        client = FakeClient(Stream([Event('response.created'), completed(json.dumps(GOOD))]))
        result = LiveBackend(AgentConfig('m'), client).turn(self.session, 'hi', 'initial', lambda e: None)
        request = client.requests[0]
        self.assertEqual(result, GOOD)
        self.assertEqual(request['conversation'], 'conv_1')
        self.assertTrue(request['stream'])
        self.assertEqual(request['tools'], [])
        self.assertEqual(request['text']['format']['name'], 'sales_comparison')
        self.assertTrue(client.stream.closed)

    def test_hosted_requires_the_tool(self):
        client = FakeClient(Stream([completed(json.dumps(GOOD))]))
        LiveBackend(AgentConfig('m'), client).turn({**self.session, 'environment': 'hosted'}, 'hi',
                                                   'initial', lambda e: None)
        self.assertEqual(client.requests[0]['tool_choice'], 'required')

    def test_failed_incomplete_error_and_missing_completion(self):
        for events in ([Event('response.failed')], [Event('response.incomplete')], [Event('error')],
                       [Event('response.created')], [completed('not json')], [completed('')]):
            with self.subTest(events=[e.type for e in events]):
                stream = Stream(events)
                with self.assertRaises(TurnFailed):
                    LiveBackend(AgentConfig('m'), FakeClient(stream)).turn(self.session, 'hi', 'initial',
                                                                          lambda e: None)
                self.assertTrue(stream.closed)

    def test_ctrl_c_becomes_cancelled(self):
        stream = Stream([Event('response.created')], interrupt=True)
        with self.assertRaises(TurnCancelled):
            LiveBackend(AgentConfig('m'), FakeClient(stream)).turn(self.session, 'hi', 'initial', lambda e: None)
        self.assertTrue(stream.closed)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name
        self.task = Path(self.tmp.name) / 'task.json'
        self.task.write_text(json.dumps(TASK), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        return quiet(main, ['--state-dir', self.state, *args])

    def test_run_follow_up_status_history(self):
        code, out, _ = self.cli('run', '--session', 'a', '--task', str(self.task))
        self.assertEqual(code, 0)
        self.assertIn('[OUTCOME] completed_and_verified', out)
        code, out, _ = self.cli('follow-up', '--session', 'a')
        self.assertEqual(code, 0)
        self.assertIn('percentage_gap  33.33', out)
        code, out, _ = self.cli('status', '--session', 'a')
        self.assertIn('#2 follow-up completed_and_verified', out)
        code, out, _ = self.cli('history', '--session', 'a')
        self.assertEqual(out.count('"role": "user"'), 2)
        events = Path(self.state, 'demo', 'a-turn-1.events.jsonl').read_text().splitlines()
        self.assertEqual(json.loads(events[-1])['type'], 'demo.response.completed')

    def test_sessions_are_independent(self):
        self.cli('run', '--session', 'a', '--task', str(self.task))
        self.cli('run', '--session', 'b', '--task', str(self.task))
        store = SessionStore(Path(self.state), 'demo')
        self.assertNotEqual(store.load('a')['conversation_id'], store.load('b')['conversation_id'])

    def test_duplicate_session_is_rejected(self):
        self.cli('run', '--session', 'a', '--task', str(self.task))
        code, _, err = self.cli('run', '--session', 'a', '--task', str(self.task))
        self.assertEqual(code, 1)
        self.assertIn('already exists', err)

    def test_failed_and_cancelled_outcomes_are_recorded(self):
        self.assertEqual(self.cli('run', '--session', 'f', '--task', str(self.task), '--simulate', 'failed')[0], 1)
        self.assertEqual(self.cli('run', '--session', 'c', '--task', str(self.task), '--simulate', 'cancelled')[0], 130)
        store = SessionStore(Path(self.state), 'demo')
        self.assertEqual(store.load('f')['turns'][-1]['status'], 'failed')
        self.assertEqual(store.load('c')['turns'][-1]['status'], 'cancelled')
        code, _, err = self.cli('follow-up', '--session', 'c')
        self.assertEqual(code, 1)
        self.assertIn('no verified initial turn', err)

    def test_interrupted_follow_up_needs_retry(self):
        self.cli('run', '--session', 'a', '--task', str(self.task))
        store = SessionStore(Path(self.state), 'demo')
        session = store.load('a')
        session['turns'].append({'number': 2, 'kind': 'follow-up', 'status': 'running'})
        store.save(session)
        code, _, err = self.cli('follow-up', '--session', 'a')
        self.assertEqual(code, 1)
        self.assertIn('--retry', err)
        self.assertEqual(self.cli('follow-up', '--session', 'a', '--retry')[0], 0)

    def test_failed_checks_exit_code_2(self):
        store = SessionStore(Path(self.state), 'demo')
        session = dict(name='x', conversation_id='c', model='m', environment='none',
                       task=TaskSpec.from_dict(TASK).to_dict(), items=[], turns=[])
        backend = SimpleNamespace(turn=lambda *a: {**GOOD, 'difference': '31'})
        with contextlib.redirect_stdout(io.StringIO()):
            _, checks = execute_turn(store, backend, session, 'p', 'initial')
        self.assertFalse(all(c.passed for c in checks))
        self.assertEqual(store.load('x')['turns'][-1]['status'], 'completed_checks_failed')

    def test_bad_session_name(self):
        code, _, err = self.cli('run', '--session', '../evil', '--task', str(self.task))
        self.assertEqual(code, 1)
        self.assertIn('Session names', err)

    def test_simulate_is_demo_only(self):
        code, _, err = self.cli('--backend', 'live', 'run', '--session', 'a', '--task', str(self.task),
                                '--simulate', 'failed')
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
