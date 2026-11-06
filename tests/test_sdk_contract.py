"""Exercise the installed OpenAI SDK over mock HTTP: no network, no real key."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import AgentConfig, LiveBackend

HAS_SDK = importlib.util.find_spec('openai') is not None
RESULT = {'higher_region': 'North', 'lower_region': 'South', 'difference': '30',
          'percentage_gap': '33.33', 'summary': 'North is 30 higher (120 - 90 = 30); gap 33.33%.'}


@unittest.skipUnless(HAS_SDK, 'Optional live dependencies are not installed.')
class SDKContractTests(unittest.TestCase):
    def client(self, handler):
        import httpx
        from openai import OpenAI
        return OpenAI(api_key='test-not-a-real-key',
                      http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_stream_is_parsed_and_request_has_the_lesson_shape(self):
        captured = []
        response = {
            'id': 'resp_test', 'object': 'response', 'created_at': 0, 'model': 'test-model',
            'status': 'completed', 'tools': [], 'tool_choice': 'auto', 'parallel_tool_calls': True,
            'output': [{'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': json.dumps(RESULT), 'annotations': []}]}],
        }

        def handler(request):
            captured.append(json.loads(request.content))
            body = ''.join(
                f'event: {kind}\ndata: {json.dumps(dict(type=kind, sequence_number=i, response=response))}\n\n'
                for i, kind in enumerate(('response.created', 'response.completed')))
            import httpx
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)

        seen = []
        with self.client(handler) as client:
            result = LiveBackend(AgentConfig('test-model'), client).turn(
                {'conversation_id': 'conv_test', 'environment': 'hosted'}, 'Also show the percentage gap.',
                'follow-up', lambda event: seen.append(event['type']))
        self.assertEqual(result, RESULT)
        self.assertEqual(seen, ['response.created', 'response.completed'])
        request = captured[0]
        self.assertEqual(request['conversation'], 'conv_test')
        self.assertTrue(request['stream'])
        self.assertEqual(request['tool_choice'], 'required')
        self.assertEqual(request['tools'][0]['type'], 'code_interpreter')
        self.assertTrue(request['text']['format']['strict'])

    def test_conversation_create_retrieve_and_paginated_history(self):
        import httpx
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path, dict(request.url.params)))
            conversation = {'id': 'conv_test', 'object': 'conversation', 'created_at': 0,
                            'metadata': {'lesson': '1'}}
            if request.url.path.endswith('/items'):
                second = 'after' in request.url.params
                item_id = 'msg_2' if second else 'msg_1'
                item = {'id': item_id, 'type': 'message', 'role': 'user', 'status': 'completed',
                        'content': [{'type': 'input_text', 'text': 'hello'}]}
                return httpx.Response(200, json={'object': 'list', 'data': [item], 'first_id': item_id,
                                                 'last_id': item_id, 'has_more': not second})
            return httpx.Response(200, json=conversation)

        with self.client(handler) as client:
            backend = LiveBackend(AgentConfig('test-model'), client)
            conversation_id = backend.create()
            state = backend.retrieve({'conversation_id': conversation_id})
            items = list(backend.history({'conversation_id': conversation_id}))
        self.assertEqual(conversation_id, 'conv_test')
        self.assertEqual(state['metadata'], {'lesson': '1'})
        self.assertEqual([item['id'] for item in items], ['msg_1', 'msg_2'])
        self.assertEqual(calls[0][:2], ('POST', '/v1/conversations'))
        self.assertEqual(calls[-1][2].get('after'), 'msg_1')


if __name__ == '__main__':
    unittest.main()
