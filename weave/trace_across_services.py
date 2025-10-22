from contextlib import contextmanager
import multiprocessing as mp
from multiprocessing.connection import Connection
import os
from typing import Optional
from dataclasses import dataclass

import weave
from weave.trace.weave_client import Call
from weave.trace.context.call_context import get_current_call, set_call_stack

## NOTE: Need to autheticate to the Capital One Instance before running this
## and set the WANDB_BASE_URL environment variable to the Capital One Instance URL
## using the below example
## os.environ['WANDB_BASE_URL'] = 'https://wandb.cloud.capitalone.com'

@contextmanager
def parent_call(trace_id: str, parent_call_id: str):
    if os.environ.get("DISABLE_PARENT_CALL"):
        yield
    else:
        with set_call_stack([Call(
                trace_id=trace_id,
                id=parent_call_id,
                # These values are sadly required, but can be ignored
                _op_name="", # can be ignored
                project_id="", # can be ignored
                parent_id=None, # can be ignored
                inputs={} # can be ignored
        )]):
            yield


@dataclass
class Payload:
    """Data structure for inter-process communication."""
    value: int
    trace_id: str
    parent_call_id: str
    
    def __str__(self) -> str:
        return (f"Payload(value={self.value}, trace_id={self.trace_id}, "
                f"parent_call_id={self.parent_call_id})")


def top_level_service(number: int, main_to_p2: Connection, main_from_p2: Connection) -> int:
    weave.init("multi_node_example")

    def simulated_remote_call_to_middle_service(value: int, trace_id: str, parent_call_id: str) -> int:
        # Send to process two
        main_to_p2.send(Payload(
            value=value,
            trace_id=trace_id,
            parent_call_id=parent_call_id
        ))
        # Wait for result from process two
        return main_from_p2.recv().value

    @weave.op
    def top_level_op(value: int) -> int:
        curr_call = get_current_call()
        result_1 = simulated_remote_call_to_middle_service((value // 2), curr_call.trace_id, curr_call.id)
        result_2 = simulated_remote_call_to_middle_service((value * -1), curr_call.trace_id, curr_call.id)
        res = result_1 + result_2
        return {
            "initial_value": number,
            "final_value": res,
            "expected_value": ((number // 2) + 2) * 2 + ((number * -1) + 2) * 2,
            "formula": f"final_value = (((X // 2) + 2) * 2) + (((X * -1) + 2) * 2)"
        }
    
    result = top_level_op(number)

    return result


def middle_level_service(payload: Payload, pipe_to_three: Connection, pipe_from_three: Connection) -> Payload:
    weave.init("multi_node_example")

    def simulated_remote_call_to_bottom_service(value: int, trace_id: str, parent_call_id: str) -> int:
        # Send to process three
        pipe_to_three.send(Payload(
            value=value,
            trace_id=trace_id,
            parent_call_id=parent_call_id
        ))
        # Wait for result from process three
        return pipe_from_three.recv().value
    
    @weave.op
    def middle_level_op(value: int) -> int:
        # Represents a service call
        curr_call = get_current_call()
        new_val = simulated_remote_call_to_bottom_service(value, curr_call.trace_id, curr_call.id)
        return new_val * 2
    
    with parent_call(payload.trace_id, payload.parent_call_id):
        result = middle_level_op(payload.value)

    return Payload(
        value=result,
        trace_id=payload.trace_id,
        parent_call_id=payload.parent_call_id
    )

 

def bottom_level_service(payload: Payload) -> Payload:
    weave.init("multi_node_example")

    @weave.op
    def lower_level_op(value: int) -> int:
        return value + 2
    
    with parent_call(payload.trace_id, payload.parent_call_id):
        result = lower_level_op(payload.value)

    return Payload(
        value=result,
        trace_id=payload.trace_id,
        parent_call_id=payload.parent_call_id
    )


#  Underlying Mechanics - Not part of the demo

def process_three(pipe_in: Connection, pipe_out: Connection) -> None:
    """
    Third process that adds 2 to received payload value and sends result back.
    
    Args:
        pipe_in: Pipe connection to receive data
        pipe_out: Pipe connection to send results
    """
    while True:
        try:
            payload: Payload = pipe_in.recv()
            result = bottom_level_service(payload)
            pipe_out.send(result)
        except EOFError:
            break

def process_two(pipe_in: Connection, pipe_to_three: Connection, 
                pipe_from_three: Connection, pipe_out: Connection) -> None:
    """
    Second process that doubles received payload value and forwards to third process.
    
    Args:
        pipe_in: Pipe connection to receive data
        pipe_to_three: Pipe connection to send data to process three
        pipe_from_three: Pipe connection to receive data from process three
        pipe_out: Pipe connection to send final results to main
    """
    while True:
        try:
            payload: Payload = pipe_in.recv()
            result = middle_level_service(payload, pipe_to_three, pipe_from_three)
            pipe_out.send(result)
        except EOFError:
            break

def get_user_input() -> Optional[int]:
    """Gets numeric input from user.
    
    Returns:
        int if valid input, None otherwise
    """
    try:
        user_input = input("Enter a number (or 'q' to quit): ")
        if user_input.lower() == 'q':
            return None
        return int(user_input)
    except ValueError:
        print("Please enter a valid number")
        return None


def main():
    # Create pipes for communication between processes
    main_to_p2, p2_from_main = mp.Pipe()  # Main -> Process 2
    p2_to_p3, p3_from_p2 = mp.Pipe()      # Process 2 -> Process 3
    p3_to_p2, p2_from_p3 = mp.Pipe()      # Process 3 -> Process 2
    p2_to_main, main_from_p2 = mp.Pipe()   # Process 2 -> Main

    # Create and start processes
    process_2 = mp.Process(
        target=process_two, 
        args=(p2_from_main, p2_to_p3, p2_from_p3, p2_to_main)
    )
    process_3 = mp.Process(
        target=process_three,
        args=(p3_from_p2, p3_to_p2)
    )
    
    process_2.start()
    process_3.start()

    try:
        while True:
            number = get_user_input()
            if number is None:
                break
            result = top_level_service(number, main_to_p2, main_from_p2)
            print(result)
            
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        # Clean up processes and pipes
        for pipe in [main_to_p2, p2_from_main, p2_to_p3, p3_from_p2, 
                    p3_to_p2, p2_from_p3, p2_to_main, main_from_p2]:
            pipe.close()
        
        process_2.terminate()
        process_3.terminate()
        process_2.join()
        process_3.join()

if __name__ == "__main__":
    main()