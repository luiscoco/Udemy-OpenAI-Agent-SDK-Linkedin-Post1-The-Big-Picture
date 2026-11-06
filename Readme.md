# Lesson 1 Practical Application: Goal-to-Result Task Runner

A small Python command-line application for **OpenAI Agents API: The Big Picture**.

You give it a vague goal ("compare regional sales and explain the difference") written as a **task contract**. The application sends the task to an agent and **streams the events** while the agent works. It then **checks the result independently** (North 120 − South 90 = **30**) and **continues the same session** with a follow-up: *"Also show the percentage gap."* (30 / 90 × 100 = **33.33%**).

The application covers every step of Lesson 1 in one runnable flow, from a user goal to a completed, verified task.

## What this application teaches

| Lesson | Concept | What the application does |
| --- | --- | --- |
| 1 | The big picture | One flow: **define → start → work → observe → continue**. Your application owns the goal, the access, and the verification. The API runs the model and keeps the conversation state. |
| 1A | Turn a goal into a task | `task.json` is a task contract with a goal, input data, a deliverable, success checks, and boundaries. The `task` command shows the prompt it becomes. |
| 1B | Configure a reusable agent | `AgentConfig` holds the model and analyst instructions. Every session reuses the same behavior with its own input. |
| 1C | Start a session, preserve context | `run` creates a conversation and saves its ID under a local session name. `status` shows the saved state. `follow-up` reuses the ID, so the original numbers are **not** sent again. |
| 1D | Give the agent a workspace | `--environment none` gives the model no execution tools. `--environment hosted` adds Code Interpreter, a managed Python sandbox. |
| 1E | Observe progress with events | Events are printed live and saved to a JSONL log for each turn. Outcomes are recorded as **completed**, **failed**, or **cancelled**. |
| 1F | Review results and continue | `verify_result` re-checks the arithmetic, the comparison, and that no numbers were invented. `history` lists the saved items. `follow-up` asks for the percentage gap in the same session. |

### How this maps to the slides

The slides show conceptual `client.beta.agents...` calls. **This application does not copy those names.** They don't exist in the OpenAI Python SDK this project was checked with (2.54.0). Instead, it uses the public, documented APIs that provide the same building blocks:

| Slide concept | Implementation in this app |
| --- | --- |
| Agent (model + instructions) | `AgentConfig`, an application-level configuration sent on every turn |
| Session | A **Conversations API** conversation (`client.conversations.create()`) |
| Turn / input | A **Responses API** request with `conversation=<id>` |
| Environment `none` / `openai_hosted` | No tools / the **Code Interpreter** tool with an `auto` container |
| Event stream | `stream=True` on `client.responses.create(...)` |
| Session items | `client.conversations.items.list(...)` |

Lesson 2 uses the same approach, so the two lessons build on each other.

## Requirements

- Python 3.10 or newer.
- **Demo mode** (default) needs only the standard library. It makes no API calls and uses no key, internet connection, or paid usage.
- **Live mode** needs the packages in `requirements.txt` and an OpenAI API key. It also needs a model your project can use with the Responses API and structured outputs. The `hosted` environment additionally needs Code Interpreter support. Live calls use your API account and may incur charges.

## Project structure

```text
practical_application/
  app.py                      # The whole application (CLI, task contract, backends, verification)
  task.json                   # 1A task contract: edit it to try other goals and data
  requirements.txt            # Live-mode dependencies
  .env.example                # Configuration template (copy to .env)
  .gitignore                  # Keeps .env, .venv and local session data out of Git
  Readme.md                   # This guide
  tests/test_app.py           # Offline tests (fake client, no network)
  tests/test_sdk_contract.py  # Real OpenAI SDK against mock HTTP (no network)
  .lesson_state/              # Created at run time: session records + event logs
```

## Run it now: offline demo

Open a terminal **in this folder** (`practical_application`) and run:

```powershell
python app.py task
python app.py run --session lesson1
python app.py follow-up --session lesson1
python app.py status --session lesson1
python app.py history --session lesson1
```

### Step 1: see the task contract (1A)

`python app.py task` makes no API call. It prints the contract, the exact prompt the agent will receive, and the values the checks will expect:

```text
[PROMPT SENT AS THE FIRST TURN]
Goal: Compare regional sales and explain the difference.
Data (use only these values): {"North": "120", "South": "90"}
Deliverable: A concise comparison with the correct numbers and a clear explanation.
Success checks:
- Use only the supplied values.
- Show the absolute difference.
- State which region is higher and by how much.
Boundaries:
- Do not guess missing data.
- Do not browse or use external data.

[WHAT THE CHECKS WILL EXPECT]
  higher=North lower=South difference=30 percentage_gap=33.33
```

### Step 2: start a session and observe the events (1C, 1E)

`python app.py run --session lesson1` prints:

```text
[DEMO] Simulated events and answers. No API calls or sandbox execution.
Session lesson1 -> conversation demo_conv_478b7067de624cec | environment: none | turn 1 (initial)
[event] demo.response.created
[event] demo.response.in_progress
{"higher_region": "North", "lower_region": "South", "difference": "30", "percentage_gap": null, "summary": "North sales are 30 higher than South (120 - 90 = 30)."}
[event] demo.response.completed

[RESULT]
  higher_region   North
  lower_region    South
  difference      30
  percentage_gap  None
  summary         North sales are 30 higher than South (120 - 90 = 30).
[CHECKS]
  [PASS] correct_arithmetic: expected 30, received '30'
  [PASS] clear_comparison: expected higher=North, lower=South; received higher='North', lower='South'
  [PASS] no_invented_values: only supplied or derived numbers
[OUTCOME] completed_and_verified
```

The `[event]` lines are lifecycle events. The JSON line is the streamed output text arriving piece by piece. The `[CHECKS]` lines are your application's own verification. They don't come from the model.

### Step 3: continue the same session (1F)

`python app.py follow-up --session lesson1` sends only *"Also show the percentage gap…"*. It doesn't send the sales figures again; the session already holds them:

```text
  percentage_gap  33.33
  summary         North sales are 30 higher than South (120 - 90 = 30). Percentage gap: 30 / 90 * 100 = 33.33%.
[CHECKS]
  ...
  [PASS] percentage_gap: expected 33.33, received '33.33'
[OUTCOME] completed_and_verified
```

### Step 4: inspect the session (1C, 1F)

- `status` shows the conversation ID, model, environment, and the outcome of every turn.
- `history` lists the saved conversation items, oldest first: your messages, the agent's answers, and any tool calls.

### Try the other outcomes (1E)

The demo backend can simulate every outcome, so you can see how the application handles each one:

```powershell
python app.py run --session sandbox_demo --environment hosted   # tool step appears in the events and history
python app.py run --session broken --simulate failed            # [event] demo.response.failed -> exit code 1
python app.py run --session stopped --simulate cancelled        # output stops midway -> exit code 130
python app.py follow-up --session stopped                       # refused: no verified initial turn
```

In demo mode, **no container is created and no code is executed**. The `demo.*` events and the `simulated_tool_call` history record only make the steps visible. Demo answers are computed deterministically from `task.json`, without a language model.

### Try your own task (1A)

Edit `task.json` (for example `"North": 125.50, "South": 89.25`). Run `python app.py task` to predict the expected values, then start a **new** session name. Each region needs a name made of letters, and amounts can have at most two decimal places.

## Configure and run live mode

### 1. Create a virtual environment and install the dependencies

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

### 2. Fill in `.env`

```dotenv
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=your_model_id_here
```

Use a model your project can access that supports the Responses API and structured outputs. Environment variables already set in your shell take precedence over `.env`. **Never commit or share `.env`**; the provided `.gitignore` excludes it.

### 3. Run the same flow against the real API

Put `--backend live` **before** the command:

```powershell
.\.venv\Scripts\python.exe app.py --backend live run --session live1
.\.venv\Scripts\python.exe app.py --backend live follow-up --session live1
.\.venv\Scripts\python.exe app.py --backend live status --session live1
.\.venv\Scripts\python.exe app.py --backend live history --session live1
```

To run the arithmetic in the real hosted Python sandbox (1D):

```powershell
.\.venv\Scripts\python.exe app.py --backend live run --session live_hosted --environment hosted
```

You will now see real event types, such as `response.created`, `response.output_text.delta`, `response.code_interpreter_call.in_progress` (hosted only), and `response.completed`. Press **Ctrl+C** during a live turn to see the *cancelled* outcome. The application stops listening, but remote work may already have happened.

On macOS/Linux, use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The turn completed **and** every check passed |
| `2` | The turn completed, but at least one check **failed**. Don't trust the result. |
| `1` | Error or failed turn (the message explains why) |
| `130` | Cancelled |

## How the application was built

The whole application is in `app.py`, organized in the order of the lessons. The snippets below are taken from it, sometimes shortened.

### 1. The task contract (1A)

```python
@dataclass(frozen=True)
class TaskSpec:
    goal: str
    data: tuple[tuple[str, Decimal], tuple[str, Decimal]]
    deliverable: str
    checks: tuple[str, ...]
    boundaries: tuple[str, ...]

    def prompt(self) -> str:
        lines = [f'Goal: {self.goal}',
                 'Data (use only these values): ' + json.dumps({n: str(v) for n, v in self.data}),
                 f'Deliverable: {self.deliverable}', 'Success checks:']
        ...
```

`TaskSpec.from_file` loads `task.json` and **validates it before anything is sent**. It requires exactly two regions with distinct names, and each amount must be finite, between 0 and one billion, with at most two decimals. The checks and boundaries go into the prompt, so the agent knows what success means. The same contract is also stored in the session record, so the application can verify the answer later. A clear task gives you something concrete to evaluate.

### 2. Reusable agent behavior (1B)

```python
@dataclass(frozen=True)
class AgentConfig:
    """Stable behavior reused by every session (an application-level configuration)."""
    model: str
    instructions: str = INSTRUCTIONS
```

`INSTRUCTIONS` describes a careful sales analyst. It must use only supplied values, show the calculation, never invent data, and return a fixed JSON shape. The behavior is defined once and every session reuses it; each session brings its own input. A session keeps the model it started with, even if you later change `OPENAI_MODEL`.

The answer's shape is fixed with a **strict JSON schema**:

```python
RESULT_FORMAT = {
    'type': 'json_schema', 'name': 'sales_comparison', 'strict': True,
    'schema': {'type': 'object', 'properties': {
        'higher_region': {'type': 'string'}, 'lower_region': {'type': 'string'},
        'difference': {'type': 'string'}, 'percentage_gap': {'type': ['string', 'null']},
        'summary': {'type': 'string'}}, ...}}
```

Structured output guarantees the **format**. It does not guarantee that the numbers are **correct**; that is why step 6 exists.

### 3. Start a session and save its ID (1C)

```python
def create(self) -> str:
    return self.client.conversations.create(metadata={'lesson': '1'}).id
```

`run` creates the conversation and immediately writes `{name → conversation_id, model, environment, task, turns}` to `.lesson_state/<backend>/<name>.json`. Because the ID is saved, `follow-up`, `status`, and `history` work from a **new process**, even after you restart your terminal.

Each turn is recorded with `status: "running"` **before** it is sent:

```python
session['turns'].append({'number': number, 'kind': kind, 'attempt_id': uuid4().hex,
                         'status': 'running', 'events': log_path.name})
store.save(session)
```

If the process dies mid-turn, the record still says `running`. `status` then warns you that a session existing is not proof that its turn finished. `follow-up` also refuses to send again until you check `history` and pass `--retry`, which avoids sending the same instruction twice. This is the local equivalent of the idempotency key on slide 1F.

### 4. Choose the environment (1D)

```python
def tools_for(environment: str) -> list[dict[str, Any]]:
    if environment == 'none':
        return []
    if environment == 'hosted':
        return [{'type': 'code_interpreter', 'container': {'type': 'auto'}}]
    raise ValueError('Environment must be none or hosted.')
```

With `none`, the model can only reason and answer. With `hosted`, it gets a managed Python sandbox, and `tool_choice='required'` makes it actually use the sandbox. Your application still decides which tools exist. Note that `none` restricts the **model** only; this local app still reads and writes its own files.

### 5. Send a turn and observe the events (1E)

```python
stream = self.client.responses.create(
    model=self.config.model, instructions=instructions,
    conversation=session['conversation_id'], input=prompt,
    tools=tools, tool_choice='required' if tools else 'auto',
    text={'format': RESULT_FORMAT}, stream=True,
)
for event in stream:
    emit(event.model_dump(mode='json'))
    if event.type == 'response.completed':
        final = event.response
    elif event.type == 'response.failed':
        raise TurnFailed(...)
```

This single request contains everything from Lesson 1. The model and instructions are the **agent**, `conversation` is the **session**, `tools` is the **environment**, `input` is the **task**, and `stream=True` provides the **events**.

`emit` writes every event to `<session>-turn-<n>.events.jsonl` and prints it. Printing only *observes* an event; it never runs a tool again. The real code also treats `response.incomplete` and `error` as failures, and so is a stream that ends without `response.completed`. It turns Ctrl+C into a *cancelled* outcome and always closes the stream in a `finally` block. An idle or finished stream is **not** proof of success; the application checks the final status and output.

### 6. Verify the result yourself (1F)

```python
def verify_result(result, spec, want_percentage=False) -> list[Check]:
    exp = expected_values(spec)                      # computed locally with Decimal
    checks.append(Check('correct_arithmetic', as_decimal(result['difference']) == exp['difference'], ...))
    checks.append(Check('clear_comparison', <higher/lower regions match>, ...))
    invented = [n for n in numbers_in(result['summary']) if abs(n) not in allowed]
    checks.append(Check('no_invented_values', not invented, ...))
    if want_percentage:
        checks.append(Check('percentage_gap', abs(pct - exp['percentage']) <= Decimal('0.01'), ...))
```

These are the three success checks from slide 1A, turned into code:

1. **Correct arithmetic:** the difference must equal |North − South|, computed with exact `Decimal` arithmetic.
2. **Clear comparison:** the higher and lower regions must be the right ones.
3. **No invented values:** every number in the summary must be a supplied value or a value derived from them (the difference, the total, the percentage, or 100). Any other number fails the check.

The follow-up adds a fourth check for the percentage gap. If any check fails, the turn is recorded as `completed_checks_failed` and the app exits with code `2`. *Completed* and *correct* are different things.

### 7. Review the history and continue (1F)

```python
for item in self.client.conversations.items.list(
    conversation_id=session['conversation_id'], order='asc', limit=100,
):
    yield item.model_dump(mode='json')
```

The SDK iterator follows later pages automatically. Saved items (messages and tool calls) are the conversation's **record**. They are not a replay of every streamed event, which is why `history` and the event logs contain different things.

The follow-up only sends `FOLLOW_UP_PROMPT` to the **same** `conversation`, which shows that the session preserved the context from the first turn.

### 8. Two backends, one flow

`DemoBackend` and `LiveBackend` have the same methods: `create`, `retrieve`, `turn`, and `history`. `execute_turn` and the CLI don't know which one they are using. That is how the demo can teach the exact flow of the live version without a key. It is also how the tests replace the network with a fake client.

## Tests

Run the offline tests (no API key needed):

```powershell
python -m unittest discover -s tests -v
```

They cover the task contract validation, every verification check, the request shape sent to the API, and the failed, incomplete, error, and cancelled outcomes. They also cover follow-up continuity, independent sessions, the `--retry` guard, and invalid input.

With the live dependencies installed (`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`), two more tests run the **real OpenAI SDK** against mock HTTP responses. These cover stream parsing, conversation creation and retrieval, and paginated history. They skip automatically if the SDK isn't installed. They were checked with OpenAI Python SDK 2.54.0. A real paid run needs your own account.

## Troubleshooting

- **"That session already exists"**: use `follow-up`, or choose a new session name.
- **"Install live dependencies"** or **"Set OPENAI_API_KEY and OPENAI_MODEL"**: install `requirements.txt` into the Python you are using and fill in `.env`.
- **Authentication, model, or tool errors**: check your key, project access, billing, and that the model supports structured outputs (and Code Interpreter for `hosted`). Try `--environment none` to rule out a tool problem.
- **Exit code 2 / `[FAIL]` lines**: the model answered, but the answer did not pass the checks. Inspect the summary, the event log, and `history`.
- **"The previous attempt was interrupted"**: run `history` to see whether the message arrived, then use `follow-up --retry` only if it didn't.
- **Cleaning up**: deleting `.lesson_state/` removes only the local records. It does not delete conversations stored in your OpenAI account.
- Automatic SDK retries are disabled (`max_retries=0`) to keep the teaching flow visible. Production code would add careful retry logic.

## Student exercises

1. Change the data to `125.50` and `89.25`. Predict the difference and percentage gap with `python app.py task`, then run a new session.
2. Make both regions equal. What do `higher_region` and the percentage gap become?
3. Set South to `0`. Why does the percentage gap have to be `null`?
4. In live mode, compare the event logs for a `none` turn and a `hosted` turn. Which event types are new?
5. Count the lines in an event log and the items in `history`. Explain why the numbers differ.
6. Add a fifth check: the summary must mention both region names.

## Official OpenAI documentation

- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state): conversations and follow-up turns.
- [Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses): event types while a response is generated.
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs): strict JSON schema results.
- [Code Interpreter](https://developers.openai.com/api/docs/guides/tools-code-interpreter): the hosted Python sandbox and containers.
- [List conversation items](https://developers.openai.com/api/reference/resources/conversations/subresources/items/methods/list): saved items, ordering, and pagination.
