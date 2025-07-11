from adalflow.core.component import Component
from adalflow.core.agent import Agent

from typing import (
    Any,
    Dict,
    Generator as GeneratorType,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)
from typing_extensions import TypeAlias
from pydantic import BaseModel

# Type aliases for better type hints
BuiltInType: TypeAlias = Union[str, int, float, bool, list, dict, tuple, set, None]
PydanticDataClass: TypeAlias = Type[BaseModel]
AdalflowDataClass: TypeAlias = Type[
    Any
]  # Replace with your actual Adalflow dataclass type if available

from adalflow.optim.parameter import Parameter
from adalflow.core.types import GeneratorOutput, FunctionOutput, StepOutput, Function
import logging
from adalflow.core.base_data_class import DataClass
import ast


__all__ = ["Runner"]

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)  # Changed to use Pydantic BaseModel


def _is_pydantic_dataclass(cls: Any) -> bool:
    # check whether cls is a pydantic dataclass
    return isinstance(cls, type) and issubclass(cls, BaseModel)


def _is_adalflow_dataclass(cls: Any) -> bool:
    # check whether cls is a adalflow dataclass
    return isinstance(cls, type) and issubclass(cls, DataClass)


class Runner(Component):
    """A runner class that executes an Agent instance with multi-step execution.

    It internally maintains a planner LLM and an executor and adds a LLM call to the executor as a tool for the planner.

    The output to the planner agent  call is expected to be a Function object. The planner iterates through at most
    max_steps unless the planner sets the action to "finish" then the planner returns the final response.

    If the user optionally specifies the output_type then the Runner parses the Function object to the output_type.

    Attributes:
        planner (Agent): The agent instance to execute
        config (RunnerConfig): Configuration for the runner
        max_steps (int): Maximum number of steps to execute
    """

    def __init__(
        self,
        agent: Agent,
        **kwargs,
    ) -> None:
        """Initialize runner with an agent and configuration.

        Args:
            agent: The agent instance to execute
            stream_parser: Optional stream parser
            output_type: Optional Pydantic data class type
            max_steps: Maximum number of steps to execute
        """
        super().__init__(**kwargs)
        self.agent = agent

        # get agent requirements
        self.max_steps = agent.max_steps
        self.answer_data_type = agent.answer_data_type

        self.step_history = []
        # add the llm call to the executor as a tool

    def _check_last_step(self, step: Function) -> bool:
        """Check if the last step is the finish step.

        Args:
            step_history: List of previous steps

        Returns:
            bool: True if the last step is a finish step
        """

        # Check if it's a finish step
        if step.name == "finish":
            return True

        return False

    def _process_data(
        self,
        data: Union[BuiltInType, PydanticDataClass, AdalflowDataClass],
        id: Optional[str] = None,
    ) -> T:
        """Process the generator output data field and convert to the specified pydantic data class of output_type.

        Args:
            data: The data to process
            id: Optional identifier for the output

        Returns:
            str: The processed data as a string
        """
        if not self.answer_data_type:
            print(data)
            log.info(f"answer_data_type: {self.answer_data_type}, data: {data}")
            # by default when the answer data type is not provided return the data directly
            return data

        try:
            model_output = None
            log.info(f"answer_data_type: {type(self.answer_data_type)}")
            if _is_pydantic_dataclass(self.answer_data_type):
                # data should be a string that represents a dictionary
                log.info(
                    f"initial answer returned by finish when user passed a pydantic type: {data}, type: {type(data)}"
                )
                data = str(data)
                dict_obj = ast.literal_eval(data)
                log.info(
                    f"initial answer after being evaluated using ast: {dict_obj}, type: {type(dict_obj)}"
                )
                model_output = self.answer_data_type(**dict_obj)
            elif _is_adalflow_dataclass(self.answer_data_type):
                # data should be a string that represents a dictionary
                log.info(
                    f"initial answer returned by finish when user passed a adalflow dataclass type: {data}, type: {type(data)}"
                )
                data = str(data)
                dict_obj = ast.literal_eval(data)
                log.info(
                    f"initial answer after being evaluated using ast: {dict_obj}, type: {type(dict_obj)}"
                )
                model_output = self.answer_data_type.from_dict(dict_obj)
            else:  # expect data to be a python built_in_type
                log.info(
                    f"type of answer is neither a pydantic dataclass or adalflow dataclass, answer before being casted again for safety: {data}, type: {type(data)}"
                )
                try:
                    # if the data is a python built_in_type then we can return it directly
                    # as the prompt passed to the LLM requires this
                    if not isinstance(data, self.answer_data_type):
                        raise ValueError(
                            f"Expected data of type {self.answer_data_type}, but got {type(data)}"
                        )
                    model_output = data
                except Exception as e:
                    log.error(
                        f"Failed to parse output: {data}, {e} for answer_data_type: {self.answer_data_type}"
                    )
                    model_output = None
                    raise ValueError(f"Error processing output: {str(e)}")

            # model_ouput is not pydantic or adalflow dataclass or a built in python type
            if not model_output:
                raise ValueError(f"Failed to parse output: {data}")

            return model_output

        except Exception as e:
            log.error(f"Error processing output: {str(e)}")
            raise ValueError(f"Error processing output: {str(e)}")

    @classmethod
    def _get_planner_function(self, output: GeneratorOutput) -> Function:
        """Check the planner output and return the function.

        Args:
            output: The planner output
        """
        if not isinstance(output, GeneratorOutput):
            raise ValueError(
                f"Expected GeneratorOutput, but got {type(output)}, value: {output}"
            )

        function = output.data

        if not isinstance(function, Function):
            raise ValueError(
                f"Expected Function in the data field of the GeneratorOutput, but got {type(function)}, value: {function}"
            )

        return function

    def call(
        self,
        prompt_kwargs: Dict[str, Any],
        model_kwargs: Optional[
            Dict[str, Any]
        ] = None,  # if some call use a different config
        use_cache: Optional[bool] = None,
        id: Optional[str] = None,
    ) -> Tuple[List[StepOutput], T]:
        """Execute the planner synchronously for multiple steps with function calling support.

        At the last step the action should be set to "finish" instead which terminates the sequence

        Args:
            prompt_kwargs: Dictionary of prompt arguments for the generator
            model_kwargs: Optional model parameters to override defaults
            use_cache: Whether to use cached results if available
            id: Optional unique identifier for the request

        Returns:
            Tuple containing:
                - List of step history (StepOutput objects)
                - Final processed output of type specified in self.answer_data_type
        """
        # reset the step history
        self.step_history = []

        # take in the query in prompt_kwargs
        prompt_kwargs = prompt_kwargs.copy() if prompt_kwargs else {}
        prompt_kwargs["step_history"] = (
            self.step_history
        )  # a reference to the step history

        model_kwargs = model_kwargs.copy() if model_kwargs else {}

        step_count = 0
        last_output = None

        # set maximum number of steps for the planner into the prompt
        # prompt_kwargs["max_steps"] = self.max_steps

        while step_count < self.max_steps:
            try:
                # Execute one step
                output = self.agent.planner.call(
                    prompt_kwargs=prompt_kwargs,
                    model_kwargs=model_kwargs,
                    use_cache=use_cache,
                    id=id,
                )

                function = self._get_planner_function(output)

                # execute the tool
                function_results = self._tool_execute(function)

                # create a step output
                step_ouput: StepOutput = StepOutput(
                    step=step_count,
                    action=function,
                    function=function,
                    observation=function_results.output,
                )
                self.step_history.append(step_ouput)

                if self._check_last_step(function):
                    last_output = self._process_data(function_results.output)
                    break

                log.debug(
                    "The prompt with the prompt template is {}".format(
                        self.agent.planner.get_prompt(**prompt_kwargs)
                    )
                )

                step_count += 1

            except Exception as e:
                error_msg = f"Error in step {step_count}: {str(e)}"
                log.error(error_msg)
                raise ValueError(error_msg)

        return self.step_history, last_output

    async def acall(
        self,
        prompt_kwargs: Dict[str, Any],
        model_kwargs: Optional[Dict[str, Any]] = None,
        use_cache: Optional[bool] = None,
        id: Optional[str] = None,
    ) -> Tuple[List[GeneratorOutput], T]:
        """Execute the planner asynchronously for multiple steps with function calling support.

        At the last step the action should be set to "finish" instead which terminates the sequence

        Args:
            prompt_kwargs: Dictionary of prompt arguments for the generator
            model_kwargs: Optional model parameters to override defaults
            use_cache: Whether to use cached results if available
            id: Optional unique identifier for the request

        Returns:
            Tuple containing:
                - List of step history (GeneratorOutput objects)
                - Final processed output
        """
        self.step_history = []
        prompt_kwargs = prompt_kwargs.copy() if prompt_kwargs else {}

        prompt_kwargs["step_history"] = (
            self.step_history
        )  # a reference to the step history

        model_kwargs = model_kwargs.copy() if model_kwargs else {}
        step_count = 0
        last_output = None

        while step_count < self.max_steps:
            try:
                # Execute one step asynchronously
                output = await self.agent.planner.acall(
                    prompt_kwargs=prompt_kwargs,
                    model_kwargs=model_kwargs,
                    use_cache=use_cache,
                    id=id,
                )

                function = self._get_planner_function(output)
                function_results = self._tool_execute(function)

                step_output: StepOutput = StepOutput(
                    step=step_count,
                    action=function,
                    function=function,
                    observation=function_results.output,
                )
                self.step_history.append(step_output)

                if self._check_last_step(function):
                    last_output = self._process_data(function_results.output)
                    break

                # important to ensure the prompt at each step is correct
                log.debug(
                    "The prompt with the prompt template is {}".format(
                        self.agent.planner.get_prompt(**prompt_kwargs)
                    )
                )

                step_count += 1

            except Exception as e:
                error_msg = f"Error in step {step_count}: {str(e)}"
                log.error(error_msg)
                return self.step_history, error_msg

        return self.step_history, last_output

    def stream(
        self,
        prompt_kwargs: Dict[str, Any],
        model_kwargs: Optional[Dict[str, Any]] = None,
        use_cache: Optional[bool] = None,
        id: Optional[str] = None,
    ) -> GeneratorType[Any, None, None]:
        """
        Synchronously executes the planner output and stream results.
        Optionally parse and post-process each chunk.

        Args:
            prompt_kwargs: Dictionary containing the prompt and related parameters
            model_kwargs: Optional dictionary of model-specific parameters
            use_cache: Whether to use cached results if available
            id: Optional identifier for the stream

        Yields:
            Chunks of the generated output, optionally parsed by the stream_parser
        """
        try:
            # Call the underlying model with the provided parameters
            generator_output = self.call(
                prompt_kwargs=prompt_kwargs,
                model_kwargs=model_kwargs,
                use_cache=use_cache,
                id=id,
            )

            # Check if the output has a data attribute that can be iterated over
            if hasattr(generator_output, "data") and hasattr(
                generator_output.data, "__iter__"
            ):
                if self.config.stream_parser:
                    for chunk in generator_output.data:
                        yield self.config.stream_parser(chunk)
                else:
                    log.warning("StreamParser not specified, yielding raw chunks")
                    for chunk in generator_output.data:
                        yield chunk
            else:
                log.error("Generator output does not contain iterable data")
                yield generator_output

        except Exception as e:
            log.error(f"Failed to stream generator output: {e}")
            raise  # Re-raise the exception to be handled by the caller

    # TODO implement async stream
    async def astream(
        self,
        user_query: str,
        current_objective: Optional[str] = None,
        memory: Optional[str] = None,
    ) -> GeneratorType[Any, None, None]:
        """
        Execute the planner asynchronously and stream results.
        Optionally parse and post-process each chunk.
        """
        # TODO replace Any type with StreamChunk type
        ...
        # This would require relying on the async_stream of the model_client instance of the generator and parsing that
        # using custom logic to buffer chunks and only stream when they complete a certain top-level field

    def _tool_execute(
        self,
        func: Function,
    ) -> Union[FunctionOutput, Parameter]:
        """
        Execute a tool function through the planner's tool manager.
        Handles both sync and async functions.
        """
        # TODO: understand the tool call and its support for async
        return self.agent.tool_manager.call(expr_or_fun=func, step="execute")
