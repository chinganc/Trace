from curses import wrapper
from typing import Optional, List, Dict, Callable, Union, Type, Any, Tuple
from opto.trace.nodes import GRAPH
from opto.trace.modules import Module, NodeContainer
from opto.trace.nodes import MessageNode, USED_NODES, Node, ParameterNode, ExceptionNode, node, get_op_name
from opto.trace.utils import global_functions_list, contain
from opto.trace.errors import ExecutionError, TraceMissingInputsError
import copy
import inspect
import functools
import re
import warnings
import ctypes
import sys

from collections import defaultdict

def bundle(
    description=None,
    n_outputs=1,
    node_dict="auto",
    traceable_code=False,
    wrap_output=True,
    unpack_input=True,
    trainable=False,
    catch_execution_error=True,
    allow_external_dependencies=False,
    overwrite_python_recursion=True,
):
    """
    Wrap a function as a FunModule, which returns node objects.
    The input signature to the wrapped function stays the same.
    """
    prev_f_locals = inspect.stack()[1].frame.f_locals
    def decorator(fun):
        fun_module= FunModule(
            fun=fun,
            description=description,
            n_outputs=n_outputs,
            node_dict=node_dict,
            traceable_code=traceable_code,
            wrap_output=wrap_output,
            unpack_input=unpack_input,
            trainable=trainable,
            catch_execution_error=catch_execution_error,
            allow_external_dependencies=allow_external_dependencies,
            overwrite_python_recursion=overwrite_python_recursion,
            ldict=prev_f_locals,  # Get the locals of the calling function
        )
        return fun_module
    return decorator


class trace_nodes:
    """This is a context manager for keeping track which nodes are read/used in an operator."""

    def __enter__(self):
        nodes = set()
        USED_NODES.append(nodes)
        return nodes

    def __exit__(self, type, value, traceback):
        USED_NODES.pop()


class FunModule(Module):
    """This is a decorator to trace a function. The wrapped function returns a MessageNode.

    Args:
        fun (callable): the operator to be traced.
        description (str): a description of the operator; see the MessageNode for syntax.
        n_outputs (int); the number of outputs of the operator; default is 1.
        node_dict (dict|str):
            None : (deprecated) the inputs are represented as a list of nodes.
            'auto': the inputs are represented as a dictionary, where the keys are the parameter names and the values are the nodes.
            dict : a dictionary to describe the inputs, where the key is a node used in this operator and the value is the node's name as described in description ; when node_dict is provided, all the used_nodes need to be in node_dict. Providing node_dict can give a correspondence between the inputs and the description of the operator.
        traceable_code (bool): if True, the code block is already traceable; if False, the code block is not traceable.
        wrap_output (bool): if True, the output of the operator is wrapped as a MessageNode; if False, the output is returned as is if the output is a Node.
        unpack_input (bool): if True, the input is extracted from the container of nodes; if False, the inputs are passed directly to the underlying function.
        trainable (bool): if True, the block of code is treated as a variable in the optimization
        catch_execution_error (bool): if True, the operator catches the exception raised during the execution of the operator and return ExecutionError.
        allow_external_dependencies (bool): if True, the operator allows external dependencies to be used in the operator. Namely, not all nodes used to create the output are in the inputs. In this case, the extra dependencies are stored in the info dictionary with key 'extra_dependencies'.
        overwrite_python_recursion (bool): if True, the operator allows the python recursion behavior of calling the decorated function to be overwritten. When true, applying bundle on a recursive function, would be the same as calling the function directly. When False, the Python's oriignal recursion behavior of decorated functions is preserved.
        ldict (dict): the local dictionary to execute the code block.

    """

    def __init__(
        self,
        fun: Callable,
        description: str = None,
        n_outputs: int = 1,
        node_dict: Union[dict, None, str] = "auto",
        traceable_code: bool = False,
        wrap_output: bool = True,
        unpack_input: bool = True,
        trainable=False,
        catch_execution_error=True,
        allow_external_dependencies=False,
        overwrite_python_recursion=True,
        ldict=None,
    ):

        assert ldict is None or isinstance(ldict, dict), "ldict must be a dictionary. or None"
        self.ldict = {} if ldict is None else ldict.copy()

        if traceable_code:
            # if the code is traceable, we don't need to unpack the input and there may be new nodes created in the code block.
            unpack_input = False
            allow_external_dependencies = True

        assert callable(fun), "fun must be a callable."
        assert (
            isinstance(node_dict, dict) or (node_dict is None) or (node_dict == "auto")
        ), "node_dict must be a dictionary or None or 'auto."

        # Get the source code of the function, excluding the decorator line
        source = inspect.getsource(fun)
        if '@' == source.strip()[0]:
            assert 'def ' in source, "The decorator must be followed by a function definition."
            # The usecase of
            # @bundle(...)
            # def fun(...):
            #   ...
            match = re.search(r".*(def.*)", source, re.DOTALL)
            source = match.group(1).strip()
        else:
            # The inline usecase of
            # fun = @bundle(...)fun(...)
            #   ...
            source = inspect.getsource(fun).strip()

        # Construct the info dictionary
        docstring = inspect.getdoc(fun)
        self.info = dict(
            fun_name=fun.__qualname__,
            doc=inspect.cleandoc(docstring) if docstring is not None else "",
            signature=inspect.signature(fun),
            source=source,
            output=None,
            external_dependencies=None,
            node_dict=node_dict,
        )

        if description is None:
            # Generate the description from the function name and docstring.
            description = f"[{self.info['fun_name']}] {self.info['doc']}."
        assert len(get_op_name(description)) > 0

        self._fun = fun
        self.node_dict = node_dict
        self.description = description
        if n_outputs > 1:
            warnings.warn("Setting n_outputs>1 will be deprecated.")
        self.n_outputs = n_outputs
        self.wrap_output = wrap_output
        self.unpack_input = unpack_input
        self.catch_execution_error = catch_execution_error
        self.allow_external_dependencies = allow_external_dependencies
        self.parameter = None
        self.overwrite_python_recursion = overwrite_python_recursion
        if trainable:
            assert overwrite_python_recursion, "trainable requires overwrite_python_recursion to be True."

            signature_sr = re.search(r"\s*(def.*\"\"\")", source, re.DOTALL)
            if signature_sr is None:  # if there is no docstring just take the first line
                signature = re.search(r"\s*(def.*:)", source).group(1)
            else:
                signature = signature_sr.group(1)
            self.parameter = ParameterNode(
                self.info["source"], name="__code", constraint="The code should start with:\n" + signature
            )

    def filter_global_namespaces(self, keys):
        """
        We don't import methods that already exist in our current global namespace
        """
        filtered_keys = []
        for k in keys:
            if k in globals().keys():
                continue
            else:
                filtered_keys.append(k)
        return filtered_keys

    @property
    def fun(self, *args, **kwargs):
        # This is called within trace_nodes context manager.
        if self.parameter is None:
            return self._fun
        else:
            code = self.parameter._data  # This is not traced, but we will add this as the parent later.
            # before we execute,  we should try to import all the global name spaces from the original function
            try:
                ldict = {}
                gdict = self._fun.__globals__.copy()
                gdict.update(self.ldict)
                exec(code, gdict, ldict)  # define the function
                fun_name = re.search(r"\s*def\s+(\w+)", code).group(1)
                fun = ldict[fun_name]
                fun.__globals__[fun_name] = fun  # for recursive calls

            except (SyntaxError, NameError, KeyError, OSError) as e:
                # Temporary fix for the issue of the code block not being able to be executed
                e_node = ExceptionNode(
                    e,
                    inputs={"code": self.parameter},
                    description=f"[exception] The code parameter {self.parameter.py_name} has an error.",
                    name="exception_" + self.parameter.py_name,
                    info=self.info,
                )
                raise ExecutionError(e_node)
            return fun

    @property
    def name(self):
        return get_op_name(self.description)

    def forward(self, *args, **kwargs):
        """
        All nodes used in the operator fun are added to used_nodes during
        the execution. If the output is not a Node, we wrap it as a
        MessageNode, whose inputs are nodes in used_nodes.
        """

        # _func_stack = defaultdict(list)  # stack of functions
        _bundled_func = None
        def tracer(frame, event, arg = None):
            """ This tracer modifies the local/global dict of the frame, so that
            when a recursive call of the wrapped function is made, it calls the
            unwrapped function."""
            nonlocal _bundled_func

            if frame.f_code == self._fun.__code__:  # entering the wrapped function
                # Use the original function, rather than the bundled function
                if event == 'call':  # Detect potential recursive calls
                    if frame.f_code.co_name in frame.f_locals:
                        # # the function is not defined globally at the top level
                        current_fun = frame.f_locals[frame.f_code.co_name]
                        if current_fun != self._fun:
                            update_local(frame, frame.f_code.co_name, self._fun)
                    elif frame.f_code.co_name in frame.f_globals:
                        current_fun = frame.f_globals[frame.f_code.co_name]
                        if current_fun != self._fun:
                            assert isinstance(current_fun, FunModule)
                            _bundled_func = current_fun  # save the original function
                            frame.f_globals[frame.f_code.co_name] = self._fun

                elif event == 'return':
                    if frame.f_code.co_name in frame.f_globals:
                        frame.f_globals[frame.f_code.co_name] = _bundled_func
            return tracer


        ## Execute self.fun
        with trace_nodes() as used_nodes:
            # After exit, used_nodes contains the nodes whose data attribute is read in the operator fun.
            _args, _kwargs = args, kwargs
            if self.unpack_input:  # extract data from container of nodes
                _args = to_data(args)
                _kwargs = to_data(kwargs)

            oldtracer = sys.gettrace()
            if self.overwrite_python_recursion and self.parameter is None:  # Overwrite the python recursion behavior
                sys.settrace(tracer)
            # add an except here
            if self.catch_execution_error:
                try:
                    outputs = self.fun(*_args, **_kwargs)
                except Exception as e:
                    outputs = e
            else:
                outputs = self.fun(*_args, **_kwargs)
            sys.settrace(oldtracer)

        ## Construct the inputs of the MessageNode from function inputs or the set used_nodes
        # TODO simplify this
        if self.node_dict is None:
            warnings.warn("Setting node_dict as None will be deprecated.")
            inputs = {n.name: n for n in used_nodes}

        else:  # Otherwise we represent inputs as dict
            assert self.node_dict == "auto" or isinstance(self.node_dict, dict)
            # Get the input signature of the operator fun
            spec = inspect.getcallargs(self.fun, *args, **kwargs)  # Read the input values from the input signature
            if isinstance(self.node_dict, dict):
                spec.update(self.node_dict)  # include additional nodes passed in by the user
            assert isinstance(spec, dict)

            # Construct the inputs of the MessageNode from the set used_nodes
            inputs = {}
            # args, varargs, varkw, defaults, kwonlyargs, kwonlydefaults, ann
            _, varargs, varkw, _, _, _, _ = inspect.getfullargspec(self.fun)

            def create_node(n):
                if isinstance(n, FunModule) and n.parameter is not None:
                    n = n.parameter
                return node(n)

            for k, v in spec.items():
                if k == varargs:  # unpack varargs
                    for i, n in enumerate(v):
                        inputs[f"args_{i}"] = create_node(n)
                elif k == varkw:  # unpack varkw
                    for kk, n in v.items():
                        inputs[kk] = create_node(n)
                else:
                    inputs[k] = create_node(v)
        # Nodes used to create the outputs but not in the inputs are external dependencies.
        external_dependencies = [node for node in used_nodes if not contain(inputs.values(), node)]
        self.info["external_dependencies"] = external_dependencies

        # Make sure all nodes in used_nodes are in the parents of the returned node.
        if len(external_dependencies) > 0 and not self.allow_external_dependencies:
            raise TraceMissingInputsError(
                f"Not all nodes used in the operator {self.fun} are specified as inputs of the returned node. Missing {[node.name for node in external_dependencies]} "
            )

        if not GRAPH.TRACE:
            inputs = {}  # We don't need to keep track of the inputs if we are not tracing.
        # Wrap the output as a MessageNode or an ExceptionNode
        if self.n_outputs == 1 or isinstance(outputs, Exception):
            nodes = self.wrap(outputs, inputs, external_dependencies)
        else:
            nodes = tuple(self.wrap(outputs[i], inputs, external_dependencies) for i in range(self.n_outputs))

        return nodes

    def wrap(self, output: Any, inputs: Union[List[Node], Dict[str, Node]], external_dependencies: List[Node]):
        """Wrap the output as a MessageNode of inputs as the parents."""
        # Some nodes are used in the operator fun, we need to wrap the output as a MessageNode.
        if not self.wrap_output:  # TODO do we ever use this?
            # If the output is already a Node, we don't need to wrap it.
            # NOTE User who implements fun is responsible for the graph structure.
            assert isinstance(output, Node)
            return output
        if self.parameter is not None:
            # This is a trainiable op. Create a new op eval.
            inputs.update({"__code": self.parameter})
            description = "[eval] This operator eval(__code, *args, **kwargs) evaluates the code block, where __code is the code (str) and *args and **kwargs are the arguments of the function. The output is the result of the evaluation, i.e., __code(*args, **kwargs)."
            name = "eval"
            self.info["fun_name"] = "eval"
        else:
            description = self.description
            name = self.name
        if output is None:
            return MessageNode(None, description=self.description, inputs=inputs, name=self.name, info=self.info)
        if isinstance(output, Exception):
            e_node = ExceptionNode(
                output,
                inputs=inputs,
                description=f'[exception] The operator {self.info["fun_name"]} raises an exception.',
                name="exception_" + name,
                info=self.info,
            )
            raise ExecutionError(e_node)
        else:
            info = self.info.copy()
            info["output"] = output  # We keep the original output node in case one needs to access the subgraph.
            return MessageNode(output, description=description, inputs=inputs, name=name, info=info)

    @staticmethod
    def is_valid_output(output):
        return isinstance(output, Node) or (isinstance(output, tuple) and all([isinstance(o, Node) for o in output]))

    def __get__(self, obj, objtype):
        # Support instance methods.
        return functools.partial(self.__call__, obj)




# TODO
def to_data(obj):
    """Extract the data from a node or a container of nodes."""
    # For node containers (tuple, list, dict, set, NodeContainer), we need to recursively extract the data from the nodes.
    if isinstance(obj, Node):  # base case
        return obj.data
    elif isinstance(obj, tuple):
        return tuple(to_data(x) for x in obj)
    elif isinstance(obj, list):
        return [to_data(x) for x in obj]
    elif isinstance(obj, dict):
        return {k: to_data(v) for k, v in obj.items()}
    elif isinstance(obj, set):
        return {to_data(x) for x in obj}
    elif isinstance(obj, NodeContainer):
        output = copy.copy(obj)
        for k, v in obj.__dict__.items():
            setattr(output, k, to_data(v))
        return output
    else:
        return obj

def update_local(frame, name, value):
    """ Update the value of a local variable in a frame."""
    frame.f_locals[name] = value
    ctypes.pythonapi.PyFrame_LocalsToFast(ctypes.py_object(frame), ctypes.c_int(0))


if __name__ == "__main__":
    x = node("hello")

    @bundle("[Custom] This is a test function.")
    def test(x):
        return x.data + " world"

    y = test(x)
    print(y)
    print("Parents", y.parents)
    print("Children", y.children)
    print("Level", y._level)
