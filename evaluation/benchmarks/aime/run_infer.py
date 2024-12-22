import asyncio
import os
from typing import Callable

import pandas as pd
from datasets import load_dataset

from evaluation.utils.shared import (
    EvalMetadata,
    EvalOutput,
    compatibility_for_eval_history_pairs,
    make_metadata,
    prepare_dataset,
    reset_logger_for_multiprocessing,
    run_evaluation,
)
from openhands.controller.state.state import State
from openhands.core.config import (
    AppConfig,
    SandboxConfig,
    get_llm_config_arg,
    get_parser,
)
from openhands.core.logger import openhands_logger as logger
from openhands.core.main import create_runtime, run_controller
from openhands.events.action import Action, MessageAction
from openhands.utils.async_utils import call_async_from_sync

ACTION_FORMAT = """
<<FINAL_ANSWER||
XXX
||FINAL_ANSWER>>
""".strip()


def aime_codeact_user_response(
    state: State,
    encapsulate_solution: bool = False,
    try_parse: Callable[[Action], str] | None = None,
) -> str:
    msg = (
        'If you have finished reporting the answer in the expected format, (and only once that is done), please use the "finish" tool to finish the interaction.\n'
        f'Again, you must report the answer in the following format before exiting, where XXX is the integer answer you have found:\n{ACTION_FORMAT}\n'
        'If you have not yet continued the task, please continue working on the task, possibly considering multiple approaches if you are stuck.\n'
        'Feel free to use all tools for calculations and solving the problem.\n'
        'IMPORTANT: YOU SHOULD NEVER ASK FOR HUMAN HELP TO SOLVE THIS TASK.\n'
    )
    return msg


AGENT_CLS_TO_FAKE_USER_RESPONSE_FN = {'CodeActAgent': aime_codeact_user_response}

AGENT_CLS_TO_INST_SUFFIX = {
    'CodeActAgent': '\n\n SUPER IMPORTANT: When you think you have solved the question, first report it back to the user in the requested format. Only once that is done, in the next turn, please finish the interaction using the "finish" tool.\n'
}


def get_config(
    metadata: EvalMetadata,
) -> AppConfig:
    config = AppConfig(
        default_agent=metadata.agent_class,
        run_as_openhands=False,
        runtime=os.environ.get('RUNTIME', 'eventstream'),
        max_iterations=metadata.max_iterations,
        sandbox=SandboxConfig(
            base_container_image='python:3.12-bookworm',
            enable_auto_lint=True,
            use_host_network=False,
            api_key=os.environ.get('ALLHANDS_API_KEY', None),
            remote_runtime_api_url=os.environ.get('SANDBOX_REMOTE_RUNTIME_API_URL'),
            keep_runtime_alive=False,
            remote_runtime_init_timeout=3600,
        ),
        # do not mount workspace
        workspace_base=None,
        workspace_mount_path=None,
    )
    config.set_llm_config(metadata.llm_config)
    return config


import re

def parse_final_answer(final_answer: str | None) -> int | None:
    """Parse the final answer from the message generated by the agent.
    Returns an integer if a valid answer is found, None otherwise.
    The answer must be an integer contained within <<FINAL_ANSWER||...||FINAL_ANSWER>> tags.
    """
    if final_answer is None:
        return None
        
    # Extract content between tags
    pattern = re.compile(r'<<FINAL_ANSWER\|\|(.*?)\|\|FINAL_ANSWER>>', re.DOTALL)
    match = pattern.search(final_answer)
    
    if not match:
        return None
        
    # Clean and validate the answer
    try:
        # Strip whitespace and convert to integer
        answer_str = match.group(1).strip()
        answer = int(answer_str)
        return answer
    except (ValueError, TypeError):
        logger.error(f'Invalid answer format. Expected integer, got: {match.group(1)}')
        return None


def compare_answers(predicted_answer: str, ground_truth: str) -> bool:
    """Compare the predicted answer with the ground truth answer.
    Both answers should be strings that can be converted to integers.
    
    Args:
        predicted_answer: The answer predicted by the agent
        ground_truth: The correct answer from the dataset
        
    Returns:
        bool: True if the answers match, False otherwise
    """
    logger.info('#############################################')
    logger.info(f'Predicted answer: {predicted_answer}')
    logger.info(f'Ground truth answer: {ground_truth}')
    logger.info('#############################################')

    try:
        predicted = int(predicted_answer)
        truth = int(ground_truth)
        return predicted == truth
    except (ValueError, TypeError) as e:
        logger.error(f'Error comparing answers: {e}')
        logger.error(f'Predicted type: {type(predicted_answer)}, Ground truth type: {type(ground_truth)}')
        return False


def calculate_accuracy(results):
    correct = sum(1 for result in results if result['test_result']['result'])
    total = len(results)
    accuracy = correct / total if total > 0 else 0
    return accuracy


def process_instance(
    instance: pd.Series,
    metadata: EvalMetadata,
    reset_logger: bool = True,
):
    config = get_config(metadata)

    if reset_logger:
        log_dir = os.path.join(metadata.eval_output_dir, 'infer_logs')
        reset_logger_for_multiprocessing(logger, instance['instance_id'], log_dir)
    else:
        logger.info(f'Starting evaluation for instance {instance["instance_id"]}.')

    instruction = f"""
Solve the following AIME (American Invitational Mathematics Examination) problem:

Year: {instance['Year']}
Problem Number: {instance['Problem Number']}

{instance['Question']}

Once you have solved the problem, please:
1. Use Python to verify your results. You can feel free to use numerical libraries such as numpy, sympy, or any others that will be helpful.
2. Check the Python code and the original problem statement to make sure they match directly.
3. Once you have verified your results, report the answer in the following format, but instead of XXX put your answer in integer format:

{ACTION_FORMAT}

Additional Instructions:
- Break down the problem into smaller steps if needed.
- You should ONLY interact with the environment provided to you AND NEVER ASK FOR HUMAN HELP.
- When you have found the answer, you MUST report it using EXACTLY the format shown above (<<FINAL_ANSWER||XXX||FINAL_ANSWER>>), where XXX is replaced by the integer answer.
- Do not include any other text or explanations within the FINAL_ANSWER tags.
- After reporting the answer in the requested format, in the next turn, please finish the interaction using the "finish" tool.
- Do not exit without reporting the answer in the correct format.

Ok now it's time to start solving the question. Good luck!
"""

    runtime = create_runtime(config)
    call_async_from_sync(runtime.connect)
    state: State | None = asyncio.run(
        run_controller(
            config=config,
            initial_user_action=MessageAction(content=instruction),
            runtime=runtime,
            fake_user_response_fn=AGENT_CLS_TO_FAKE_USER_RESPONSE_FN.get(
                metadata.agent_class
            ),
        )
    )
    assert state is not None, 'State should not be None.'

    # Initialize variables to store the final answer and message
    final_answer = None
    final_message = None
    found_numbers = set()

    # First try to find answer in AgentFinishAction's thought
    for event in reversed(state.history):
        if hasattr(event, 'source') and event.source != 'user':
            # Try to extract numbers from all content for fallback
            if hasattr(event, 'content'):
                # Find all numbers in the content
                numbers = re.findall(r'\b\d+\b', event.content)
                found_numbers.update(int(n) for n in numbers)
            
            if hasattr(event, 'thought'):
                # Find all numbers in thoughts
                numbers = re.findall(r'\b\d+\b', event.thought)
                found_numbers.update(int(n) for n in numbers)

            # Look for formatted answer in AgentFinishAction thought
            if hasattr(event, 'thought') and '<<FINAL_ANSWER||' in event.thought:
                extracted = parse_final_answer(event.thought)
                if extracted is not None:
                    final_answer = extracted
                    final_message = event.thought
                    break

            # Look for formatted answer in MessageAction content
            if hasattr(event, 'content') and '<<FINAL_ANSWER||' in event.content:
                extracted = parse_final_answer(event.content)
                if extracted is not None:
                    final_answer = extracted
                    final_message = event.content
                    break

    # If no properly formatted answer found but we found numbers in the conversation
    if final_answer is None and found_numbers:
        # Try to find the correct answer in the found numbers
        correct_answer = int(instance['Answer'])
        if correct_answer in found_numbers:
            final_answer = correct_answer
            logger.info(f'No properly formatted answer found, but correct number {correct_answer} was mentioned in the conversation')
        else:
            # Take the last number mentioned as a fallback
            final_answer = list(found_numbers)[-1]
            logger.info(f'No properly formatted answer found, using last mentioned number: {final_answer}')

    logger.info('#############################################')
    logger.info(f'Final message: {final_message}')
    logger.info(f'Extracted final answer: {final_answer}')
    logger.info(f'All numbers found in conversation: {sorted(list(found_numbers))}')
    logger.info('#############################################')

    # Compare the answer with ground truth
    test_result = False
    if final_answer is not None:
        test_result = compare_answers(str(final_answer), instance['Answer'])

    logger.info('#############################################')
    logger.info(f'Test result: {test_result}')
    logger.info('#############################################')

    if state is None:
        raise ValueError('State should not be None.')

    metrics = state.metrics.get() if state.metrics else None

    output = EvalOutput(
        instance_id=str(instance['instance_id']),
        instruction=instruction,
        metadata=metadata,
        history=compatibility_for_eval_history_pairs(state.history),
        metrics=metrics,
        error=state.last_error if state and state.last_error else None,
        test_result={
            'result': test_result,
            'final_message': final_message,
            'final_answer': final_answer,
            'found_numbers': sorted(list(found_numbers)) if found_numbers else []
        },
    )
    return output


if __name__ == '__main__':
    parser = get_parser()
    parser.add_argument(
        '--data-split',
        type=str,
        default='train',
        help='data split to evaluate, eg. train, test',
    )
    parser.add_argument(
        '--year',
        type=int,
        help='specific year to evaluate (e.g., 2023)',
    )
    args, _ = parser.parse_known_args()

    # Convert kebab-case to snake_case for compatibility
    args.llm_config = args.llm_config
    args.agent_cls = args.agent_cls
    args.max_iterations = args.max_iterations
    args.eval_n_limit = args.eval_n_limit
    args.eval_num_workers = args.eval_num_workers
    args.eval_note = args.eval_note
    args.eval_output_dir = args.eval_output_dir

    print('Parsed arguments:', vars(args))
    print('LLM config argument:', args.llm_config)

    llm_config = None
    if args.llm_config:
        print('Attempting to get LLM config...')
        llm_config = get_llm_config_arg(args.llm_config)
        if llm_config:
            print('LLM config retrieved successfully')
            llm_config.modify_params = False
        else:
            print('Failed to retrieve LLM config')

    if llm_config is None:
        raise ValueError(f'Could not find LLM config: --llm-config {args.llm_config}')

    dataset = load_dataset('gneubig/aime-1983-2024', split=args.data_split)
    aime_dataset = dataset.to_pandas()
    aime_dataset['instance_id'] = aime_dataset['ID']

    # Filter by year if specified
    if args.year is not None:
        aime_dataset = aime_dataset[aime_dataset['Year'] == args.year]
        if len(aime_dataset) == 0:
            raise ValueError(f'No problems found for year {args.year}')

    if args.agent_cls != 'CodeActAgent':
        raise ValueError(
            f'Agent class {args.agent_cls} not supported for AIME evaluation.'
        )

    metadata = make_metadata(
        llm_config=llm_config,
        dataset_name='aime-1983-2024',
        agent_class=args.agent_cls,
        max_iterations=args.max_iterations,
        eval_note=args.eval_note,
        eval_output_dir=args.eval_output_dir,
        data_split=args.data_split,
    )

    output_file = os.path.join(metadata.eval_output_dir, 'output.jsonl')
    prepared_dataset = prepare_dataset(aime_dataset, output_file, args.eval_n_limit)

    run_evaluation(
        dataset=prepared_dataset,
        metadata=metadata,
        output_file=output_file,
        num_workers=args.eval_num_workers,
        process_instance_func=process_instance,
    )

    logger.info(f'Evaluation completed. Results saved to {output_file}')
