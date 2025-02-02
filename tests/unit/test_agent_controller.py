import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, call
from uuid import uuid4

import pytest
from litellm import ContextWindowExceededError

from openhands.controller.agent import Agent
from openhands.controller.agent_controller import AgentController
from openhands.controller.state.state import State, TrafficControlState
from openhands.core.config import AppConfig
from openhands.core.main import run_controller
from openhands.core.schema import AgentState
from openhands.events import Event, EventSource, EventStream, EventStreamSubscriber
from openhands.events.action import (
    ChangeAgentStateAction,
    CmdRunAction,
    MessageAction,
    PromptExtensionAction,
    SystemMessageAction,
)
from openhands.events.observation import (
    ErrorObservation,
)
from openhands.events.serialization import event_to_dict
from openhands.llm import LLM
from openhands.llm.metrics import Metrics
from openhands.runtime.base import Runtime
from openhands.storage.memory import InMemoryFileStore
from openhands.utils.prompt import PromptManager


@pytest.fixture
def temp_dir(tmp_path_factory: pytest.TempPathFactory) -> str:
    return str(tmp_path_factory.mktemp('test_event_stream'))


@pytest.fixture(scope='function')
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_agent():
    agent = MagicMock(spec=Agent)
    agent.llm = MagicMock(spec=LLM)
    agent.llm.metrics = Metrics()
    agent.llm.config = AppConfig().get_llm_config()
    agent.config = MagicMock()
    agent.config.enable_prompt_extensions = False
    agent.config.disabled_microagents = []
    agent.get_prompt_manager.return_value = None
    return agent


@pytest.fixture
def mock_event_stream():
    mock = MagicMock(spec=EventStream)
    mock.get_latest_event_id.return_value = 0
    return mock


@pytest.fixture
def mock_runtime() -> Runtime:
    return MagicMock(
        spec=Runtime,
        event_stream=EventStream(sid='test', file_store=InMemoryFileStore({})),
    )


@pytest.fixture
def mock_status_callback():
    return AsyncMock()


async def send_event_to_controller(controller, event):
    await controller._on_event(event)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_set_agent_state(mock_agent, mock_event_stream):
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )
    await controller.set_agent_state_to(AgentState.RUNNING)
    assert controller.get_agent_state() == AgentState.RUNNING

    await controller.set_agent_state_to(AgentState.PAUSED)
    assert controller.get_agent_state() == AgentState.PAUSED
    await controller.close()


@pytest.mark.asyncio
async def test_on_event_message_action(mock_agent, mock_event_stream):
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )
    controller.state.agent_state = AgentState.RUNNING
    message_action = MessageAction(content='Test message')
    await send_event_to_controller(controller, message_action)
    assert controller.get_agent_state() == AgentState.RUNNING
    await controller.close()


@pytest.mark.asyncio
async def test_on_event_change_agent_state_action(mock_agent, mock_event_stream):
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )
    controller.state.agent_state = AgentState.RUNNING
    change_state_action = ChangeAgentStateAction(agent_state=AgentState.PAUSED)
    await send_event_to_controller(controller, change_state_action)
    assert controller.get_agent_state() == AgentState.PAUSED
    await controller.close()


@pytest.mark.asyncio
async def test_react_to_exception(mock_agent, mock_event_stream, mock_status_callback):
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        status_callback=mock_status_callback,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )
    error_message = 'Test error'
    await controller._react_to_exception(RuntimeError(error_message))
    controller.status_callback.assert_called_once()
    await controller.close()


@pytest.mark.asyncio
async def test_run_controller_with_fatal_error():
    config = AppConfig()
    file_store = InMemoryFileStore({})
    event_stream = EventStream(sid='test', file_store=file_store)

    agent = MagicMock(spec=Agent)

    def agent_step_fn(state):
        print(f'agent_step_fn received state: {state}')
        return CmdRunAction(command='ls')

    agent.step = agent_step_fn
    agent.llm = MagicMock(spec=LLM)
    agent.llm.metrics = Metrics()
    agent.llm.config = config.get_llm_config()
    agent.config = MagicMock()
    agent.config.enable_prompt_extensions = False
    agent.config.disabled_microagents = []
    agent.get_prompt_manager.return_value = None

    runtime = MagicMock(spec=Runtime)

    def on_event(event: Event):
        if isinstance(event, CmdRunAction):
            error_obs = ErrorObservation('You messed around with Jim')
            error_obs._cause = event.id
            event_stream.add_event(error_obs, EventSource.USER)

    event_stream.subscribe(EventStreamSubscriber.RUNTIME, on_event, str(uuid4()))
    runtime.event_stream = event_stream

    state = await run_controller(
        config=config,
        initial_user_action=MessageAction(content='Test message'),
        runtime=runtime,
        sid='test',
        agent=agent,
        fake_user_response_fn=lambda _: 'repeat',
    )
    print(f'state: {state}')
    events = list(event_stream.get_events())
    print(f'event_stream: {events}')
    assert state.iteration == 4
    assert state.agent_state == AgentState.ERROR
    assert state.last_error == 'AgentStuckInLoopError: Agent got stuck in a loop'
    assert len(events) == 11


@pytest.mark.asyncio
async def test_run_controller_stop_with_stuck():
    config = AppConfig()
    file_store = InMemoryFileStore({})
    event_stream = EventStream(sid='test', file_store=file_store)

    agent = MagicMock(spec=Agent)

    def agent_step_fn(state):
        print(f'agent_step_fn received state: {state}')
        return CmdRunAction(command='ls')

    agent.step = agent_step_fn
    agent.llm = MagicMock(spec=LLM)
    agent.llm.metrics = Metrics()
    agent.llm.config = config.get_llm_config()
    agent.config = MagicMock()
    agent.config.enable_prompt_extensions = False
    agent.config.disabled_microagents = []
    agent.get_prompt_manager.return_value = None
    runtime = MagicMock(spec=Runtime)

    def on_event(event: Event):
        if isinstance(event, CmdRunAction):
            non_fatal_error_obs = ErrorObservation(
                'Non fatal error here to trigger loop'
            )
            non_fatal_error_obs._cause = event.id
            event_stream.add_event(non_fatal_error_obs, EventSource.ENVIRONMENT)

    event_stream.subscribe(EventStreamSubscriber.RUNTIME, on_event, str(uuid4()))
    runtime.event_stream = event_stream

    state = await run_controller(
        config=config,
        initial_user_action=MessageAction(content='Test message'),
        runtime=runtime,
        sid='test',
        agent=agent,
        fake_user_response_fn=lambda _: 'repeat',
    )
    events = list(event_stream.get_events())
    print(f'state: {state}')
    for i, event in enumerate(events):
        print(f'event {i}: {event_to_dict(event)}')

    assert state.iteration == 4
    assert len(events) == 11
    # check the eventstream have 4 pairs of repeated actions and observations
    repeating_actions_and_observations = events[2:10]
    for action, observation in zip(
        repeating_actions_and_observations[0::2],
        repeating_actions_and_observations[1::2],
    ):
        action_dict = event_to_dict(action)
        observation_dict = event_to_dict(observation)
        assert action_dict['action'] == 'run' and action_dict['args']['command'] == 'ls'
        assert (
            observation_dict['observation'] == 'error'
            and observation_dict['content'] == 'Non fatal error here to trigger loop'
        )
    last_event = event_to_dict(events[-1])
    assert last_event['extras']['agent_state'] == 'error'
    assert last_event['observation'] == 'agent_state_changed'

    assert state.agent_state == AgentState.ERROR
    assert state.last_error == 'AgentStuckInLoopError: Agent got stuck in a loop'


@pytest.mark.asyncio
async def test_max_iterations_extension(mock_agent, mock_event_stream):
    # Test with headless_mode=False - should extend max_iterations
    initial_state = State(max_iterations=10)

    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=False,
        initial_state=initial_state,
    )
    controller.state.agent_state = AgentState.RUNNING
    controller.state.iteration = 10
    assert controller.state.traffic_control_state == TrafficControlState.NORMAL

    # Trigger throttling by calling _step() when we hit max_iterations
    await controller._step()
    assert controller.state.traffic_control_state == TrafficControlState.THROTTLING
    assert controller.state.agent_state == AgentState.ERROR

    # Simulate a new user message
    message_action = MessageAction(content='Test message')
    message_action._source = EventSource.USER
    await send_event_to_controller(controller, message_action)

    # Max iterations should be extended to current iteration + initial max_iterations
    assert (
        controller.state.max_iterations == 20
    )  # Current iteration (10 initial because _step() should not have been executed) + initial max_iterations (10)
    assert controller.state.traffic_control_state == TrafficControlState.NORMAL
    assert controller.state.agent_state == AgentState.RUNNING

    # Close the controller to clean up
    await controller.close()

    # Test with headless_mode=True - should NOT extend max_iterations
    initial_state = State(max_iterations=10)
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
        initial_state=initial_state,
    )
    controller.state.agent_state = AgentState.RUNNING
    controller.state.iteration = 10
    assert controller.state.traffic_control_state == TrafficControlState.NORMAL

    # Simulate a new user message
    message_action = MessageAction(content='Test message')
    message_action._source = EventSource.USER
    await send_event_to_controller(controller, message_action)

    # Max iterations should NOT be extended in headless mode
    assert controller.state.max_iterations == 10  # Original value unchanged

    # Trigger throttling by calling _step() when we hit max_iterations
    await controller._step()

    assert controller.state.traffic_control_state == TrafficControlState.THROTTLING
    assert controller.state.agent_state == AgentState.ERROR
    await controller.close()


@pytest.mark.asyncio
async def test_step_max_budget(mock_agent, mock_event_stream):
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        max_budget_per_task=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=False,
    )
    controller.state.agent_state = AgentState.RUNNING
    controller.state.metrics.accumulated_cost = 10.1
    assert controller.state.traffic_control_state == TrafficControlState.NORMAL
    await controller._step()
    assert controller.state.traffic_control_state == TrafficControlState.THROTTLING
    assert controller.state.agent_state == AgentState.ERROR
    await controller.close()


@pytest.mark.asyncio
async def test_step_max_budget_headless(mock_agent, mock_event_stream):
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        max_budget_per_task=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )
    controller.state.agent_state = AgentState.RUNNING
    controller.state.metrics.accumulated_cost = 10.1
    assert controller.state.traffic_control_state == TrafficControlState.NORMAL
    await controller._step()
    assert controller.state.traffic_control_state == TrafficControlState.THROTTLING
    # In headless mode, throttling results in an error
    assert controller.state.agent_state == AgentState.ERROR
    await controller.close()


@pytest.mark.asyncio
async def test_reset_with_pending_action_no_observation(mock_agent, mock_event_stream):
    """Test reset() when there's a pending action with tool call metadata but no observation."""
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Create a pending action with tool call metadata
    pending_action = CmdRunAction(command='test')
    pending_action.tool_call_metadata = {
        'function': 'test_function',
        'args': {'arg1': 'value1'},
    }
    controller._pending_action = pending_action

    # Call reset
    controller._reset()

    # Verify that an ErrorObservation was added to the event stream
    mock_event_stream.add_event.assert_called_once()
    args, kwargs = mock_event_stream.add_event.call_args
    error_obs, source = args
    assert isinstance(error_obs, ErrorObservation)
    assert error_obs.content == 'The action has not been executed.'
    assert error_obs.tool_call_metadata == pending_action.tool_call_metadata
    assert error_obs._cause == pending_action.id
    assert source == EventSource.AGENT

    # Verify that pending action was reset
    assert controller._pending_action is None

    # Verify that agent.reset() was called
    mock_agent.reset.assert_called_once()
    await controller.close()


@pytest.mark.asyncio
async def test_reset_with_pending_action_existing_observation(
    mock_agent, mock_event_stream
):
    """Test reset() when there's a pending action with tool call metadata and an existing observation."""
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Create a pending action with tool call metadata
    pending_action = CmdRunAction(command='test')
    pending_action.tool_call_metadata = {
        'function': 'test_function',
        'args': {'arg1': 'value1'},
    }
    controller._pending_action = pending_action

    # Add an existing observation to the history
    existing_obs = ErrorObservation(content='Previous error')
    existing_obs.tool_call_metadata = pending_action.tool_call_metadata
    controller.state.history.append(existing_obs)

    # Call reset
    controller._reset()

    # Verify that no new ErrorObservation was added to the event stream
    mock_event_stream.add_event.assert_not_called()

    # Verify that pending action was reset
    assert controller._pending_action is None

    # Verify that agent.reset() was called
    mock_agent.reset.assert_called_once()
    await controller.close()


@pytest.mark.asyncio
async def test_reset_without_pending_action(mock_agent, mock_event_stream):
    """Test reset() when there's no pending action."""
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Call reset
    controller._reset()

    # Verify that no ErrorObservation was added to the event stream
    mock_event_stream.add_event.assert_not_called()

    # Verify that pending action is None
    assert controller._pending_action is None

    # Verify that agent.reset() was called
    mock_agent.reset.assert_called_once()
    await controller.close()


@pytest.mark.asyncio
async def test_reset_with_pending_action_no_metadata(
    mock_agent, mock_event_stream, monkeypatch
):
    """Test reset() when there's a pending action without tool call metadata."""
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Create a pending action without tool call metadata
    pending_action = CmdRunAction(command='test')
    # Mock hasattr to return False for tool_call_metadata
    original_hasattr = hasattr

    def mock_hasattr(obj, name):
        if obj == pending_action and name == 'tool_call_metadata':
            return False
        return original_hasattr(obj, name)

    monkeypatch.setattr('builtins.hasattr', mock_hasattr)
    controller._pending_action = pending_action

    # Call reset
    controller._reset()

    # Verify that no ErrorObservation was added to the event stream
    mock_event_stream.add_event.assert_not_called()

    # Verify that pending action was reset
    assert controller._pending_action is None

    # Verify that agent.reset() was called
    mock_agent.reset.assert_called_once()
    await controller.close()


@pytest.mark.asyncio
async def test_run_controller_max_iterations_has_metrics():
    config = AppConfig(
        max_iterations=3,
    )
    file_store = InMemoryFileStore({})
    event_stream = EventStream(sid='test', file_store=file_store)

    agent = MagicMock(spec=Agent)
    agent.llm = MagicMock(spec=LLM)
    agent.llm.metrics = Metrics()  # Start with fresh metrics
    agent.llm.config = config.get_llm_config()
    agent.config = MagicMock()
    agent.config.enable_prompt_extensions = False
    agent.config.disabled_microagents = []
    agent.get_prompt_manager.return_value = None

    # Keep track of total cost
    total_cost = 0.0

    def agent_step_fn(state):
        print(f'agent_step_fn received state: {state}')
        # Mock the cost of the LLM
        nonlocal total_cost
        total_cost += 10.0
        state.metrics.add_cost(10.0)  # Add cost directly to state metrics
        print(
            f'state.metrics.accumulated_cost: {state.metrics.accumulated_cost}'
        )
        return CmdRunAction(command='ls')

    agent.step = agent_step_fn

    runtime = MagicMock(spec=Runtime)

    def on_event(event: Event):
        if isinstance(event, CmdRunAction):
            non_fatal_error_obs = ErrorObservation(
                'Non fatal error. event id: ' + str(event.id)
            )
            non_fatal_error_obs._cause = event.id
            event_stream.add_event(non_fatal_error_obs, EventSource.ENVIRONMENT)

    event_stream.subscribe(EventStreamSubscriber.RUNTIME, on_event, str(uuid4()))
    runtime.event_stream = event_stream

    state = await run_controller(
        config=config,
        initial_user_action=MessageAction(content='Test message'),
        runtime=runtime,
        sid='test',
        agent=agent,
        fake_user_response_fn=lambda _: 'repeat',
    )
    assert state.iteration == 3
    assert state.agent_state == AgentState.ERROR
    assert (
        state.last_error
        == 'RuntimeError: Agent reached maximum iteration in headless mode. Current iteration: 3, max iteration: 3'
    )
    assert (
        state.metrics.accumulated_cost == 10.0 * 3
    ), f'Expected accumulated cost to be 30.0, but got {state.metrics.accumulated_cost}'


@pytest.mark.asyncio
async def test_notify_on_llm_retry(mock_agent, mock_event_stream, mock_status_callback):
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        status_callback=mock_status_callback,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )
    controller._notify_on_llm_retry(1, 2)
    controller.status_callback.assert_called_once_with('info', 'STATUS$LLM_RETRY', ANY)
    await controller.close()


@pytest.mark.asyncio
async def test_context_window_exceeded_error_handling(mock_agent, mock_event_stream):
    """Test that context window exceeded errors are handled correctly by truncating history."""

    class StepState:
        def __init__(self):
            self.has_errored = False

        def step(self, state: State):
            # Append a few messages to the history -- these will be truncated when we throw the error
            state.history = [
                MessageAction(content='Test message 0'),  # First user message
                MessageAction(content='Test message 1'),  # Agent response
                MessageAction(content='Test message 2'),  # Another agent message
                MessageAction(content='Test message 3'),  # Another agent message
            ]
            state.history[0]._source = EventSource.USER

            error = ContextWindowExceededError(
                message='prompt is too long: 233885 tokens > 200000 maximum',
                model='',
                llm_provider='',
            )
            self.has_errored = True
            raise error

    state = StepState()
    mock_agent.step = state.step

    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Set the agent running and take a step in the controller -- this is similar
    # to taking a single step using `run_controller`, but much easier to control
    # termination for testing purposes
    controller.state.agent_state = AgentState.RUNNING
    await controller._step()

    # Check that the error was thrown and the history has been truncated
    assert state.has_errored
    # Should keep first user message and second half of history
    expected_history = [
        MessageAction(content='Test message 0'),  # First user message always kept
        MessageAction(content='Test message 2'),  # Second half of history
        MessageAction(content='Test message 3'),
    ]
    expected_history[0]._source = EventSource.USER
    assert len(controller.state.history) == len(expected_history)
    for actual, expected in zip(controller.state.history, expected_history):
        assert actual.content == expected.content
        assert actual.source == expected.source


@pytest.mark.asyncio
async def test_run_controller_with_context_window_exceeded(mock_agent, mock_runtime):
    """Tests that the controller can make progress after handling context window exceeded errors."""

    class StepState:
        def __init__(self):
            self.has_errored = False

        def step(self, state: State):
            # If the state has more than one message and we haven't errored yet,
            # throw the context window exceeded error
            if len(state.history) > 1 and not self.has_errored:
                error = ContextWindowExceededError(
                    message='prompt is too long: 233885 tokens > 200000 maximum',
                    model='',
                    llm_provider='',
                )
                self.has_errored = True
                raise error

            return MessageAction(content=f'STEP {len(state.history)}')

    step_state = StepState()
    mock_agent.step = step_state.step

    try:
        state = await asyncio.wait_for(
            run_controller(
                config=AppConfig(max_iterations=3),
                initial_user_action=MessageAction(content='INITIAL'),
                runtime=mock_runtime,
                sid='test',
                agent=mock_agent,
                fake_user_response_fn=lambda _: 'repeat',
            ),
            timeout=10,
        )

    # A timeout error indicates the run_controller entrypoint is not making
    # progress
    except asyncio.TimeoutError as e:
        raise AssertionError(
            'The run_controller function did not complete in time.'
        ) from e

    # Hitting the iteration limit indicates the controller is failing for the
    # expected reason
    assert state.iteration == 3
    assert state.agent_state == AgentState.ERROR
    assert (
        state.last_error
        == 'RuntimeError: Agent reached maximum iteration in headless mode. Current iteration: 3, max iteration: 3'
    )

    # Check that the context window exceeded error was raised during the run
    assert step_state.has_errored


@pytest.mark.asyncio
async def test_prompt_manager_initialization(mock_agent, mock_event_stream):
    """Test that the prompt manager is properly initialized and sends system message."""
    # Mock the prompt manager
    mock_prompt_manager = MagicMock(spec=PromptManager)
    mock_prompt_manager.get_system_message.return_value = "Test system message"
    mock_agent.get_prompt_manager.return_value = mock_prompt_manager

    # Create controller
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Verify that system message was sent
    mock_event_stream.add_event.assert_called_with(
        SystemMessageAction(content="Test system message"),
        EventSource.AGENT,
    )

    await controller.close()


@pytest.mark.asyncio
async def test_prompt_manager_extensions(mock_agent, mock_event_stream):
    """Test that prompt extensions are properly added to event stream."""
    # Mock the prompt manager and enable extensions
    mock_agent.config.enable_prompt_extensions = True
    mock_prompt_manager = MagicMock(spec=PromptManager)
    mock_prompt_manager.get_system_message.return_value = "Test system message"

    # Mock the prompt extension methods
    def add_examples(msg):
        msg.content[0].text = "Examples added: " + msg.content[0].text
    mock_prompt_manager.add_examples_to_initial_message.side_effect = add_examples

    def add_info(msg):
        msg.content[0].text = "Info added: " + msg.content[0].text
    mock_prompt_manager.add_info_to_initial_message.side_effect = add_info

    def enhance(msg):
        msg.content[0].text = "Enhanced: " + msg.content[0].text
    mock_prompt_manager.enhance_message.side_effect = enhance

    mock_agent.get_prompt_manager.return_value = mock_prompt_manager

    # Create controller
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Send a user message
    message_action = MessageAction(content="Test message")
    message_action._source = EventSource.USER
    await controller._on_event(message_action)

    # Get all calls to add_event
    actual_calls = mock_event_stream.add_event.call_args_list

    # Verify that system message was added
    assert any(
        isinstance(args[0], SystemMessageAction) and args[0].content == "Test system message"
        for args, _ in actual_calls
    )

    # Verify that prompt extensions were added
    expected_extensions = [
        ("Examples added: Test message", "examples"),
        ("Info added: Examples added: Test message", "info"),
        ("Enhanced: Info added: Examples added: Test message", "enhance"),
    ]
    for content, ext_type in expected_extensions:
        assert any(
            isinstance(args[0], PromptExtensionAction)
            and args[0].content == content
            and args[0].extension_type == ext_type
            for args, _ in actual_calls
        ), f"Missing extension: {ext_type}"



    await controller.close()


@pytest.mark.asyncio
async def test_prompt_manager_extensions_disabled(mock_agent, mock_event_stream):
    """Test that prompt extensions are not added when disabled."""
    # Mock the prompt manager but disable extensions
    mock_agent.config.enable_prompt_extensions = False
    mock_prompt_manager = MagicMock(spec=PromptManager)
    mock_prompt_manager.get_system_message.return_value = "Test system message"
    mock_agent.get_prompt_manager.return_value = mock_prompt_manager

    # Create controller
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Send a user message
    message_action = MessageAction(content="Test message")
    message_action._source = EventSource.USER
    await controller._on_event(message_action)

    # Get all calls to add_event
    actual_calls = mock_event_stream.add_event.call_args_list

    # Verify that system message was added
    assert any(
        isinstance(args[0], SystemMessageAction) and args[0].content == "Test system message"
        for args, _ in actual_calls
    )

    # Verify that no prompt extensions were added
    assert not any(
        isinstance(args[0], PromptExtensionAction)
        for args, _ in actual_calls
    )

    # Verify that extension methods were not called
    mock_prompt_manager.add_examples_to_initial_message.assert_not_called()
    mock_prompt_manager.add_info_to_initial_message.assert_not_called()
    mock_prompt_manager.enhance_message.assert_not_called()

    await controller.close()


@pytest.mark.asyncio
async def test_prompt_manager_extensions_delegate(mock_agent, mock_event_stream):
    """Test that prompt extensions are not added for delegate controllers."""
    # Mock the prompt manager
    mock_prompt_manager = MagicMock(spec=PromptManager)
    mock_prompt_manager.get_system_message.return_value = "Test system message"
    mock_agent.get_prompt_manager.return_value = mock_prompt_manager

    # Create delegate controller
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
        is_delegate=True,  # This should prevent system message and extensions
    )

    # Send a user message
    message_action = MessageAction(content="Test message")
    message_action._source = EventSource.USER
    await controller._on_event(message_action)

    # Get all calls to add_event
    actual_calls = mock_event_stream.add_event.call_args_list

    # Verify that only the original message was added (plus state changes)
    print("Message action:", message_action)
    print("Actual calls:", actual_calls)
    assert any(
        args[0] == message_action and args[1] == EventSource.USER
        for args, _ in actual_calls
    )

    # Verify that no system message or prompt extensions were added
    assert not any(
        isinstance(args[0], SystemMessageAction) or isinstance(args[0], PromptExtensionAction)
        for args, _ in actual_calls
    )

    # Verify that system message and extension methods were not called
    mock_prompt_manager.get_system_message.assert_not_called()
    mock_prompt_manager.add_examples_to_initial_message.assert_not_called()
    mock_prompt_manager.add_info_to_initial_message.assert_not_called()
    mock_prompt_manager.enhance_message.assert_not_called()

    await controller.close()


@pytest.mark.asyncio
async def test_prompt_manager_not_initialized(mock_agent, mock_event_stream):
    """Test that no system message is sent if prompt manager is not initialized."""
    # Set prompt manager to None
    mock_agent.prompt_manager = None

    # Create controller
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
    )

    # Verify that no system message was sent
    for call in mock_event_stream.add_event.call_args_list:
        args, _ = call
        assert not isinstance(args[0], SystemMessageAction)

    await controller.close()


@pytest.mark.asyncio
async def test_prompt_manager_delegate_initialization(mock_agent, mock_event_stream):
    """Test that system message is not sent for delegate controllers."""
    # Mock the prompt manager
    mock_agent.prompt_manager = MagicMock(spec=PromptManager)
    mock_agent.prompt_manager.get_system_message.return_value = "Test system message"

    # Create controller with is_delegate=True
    controller = AgentController(
        agent=mock_agent,
        event_stream=mock_event_stream,
        max_iterations=10,
        sid='test',
        confirmation_mode=False,
        headless_mode=True,
        is_delegate=True,  # This should prevent system message from being sent
    )

    # Verify that no system message was sent
    for call in mock_event_stream.add_event.call_args_list:
        args, _ = call
        assert not isinstance(args[0], SystemMessageAction)

    await controller.close()
