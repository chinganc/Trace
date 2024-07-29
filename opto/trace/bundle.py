import copy
import ctypes
import functools
import inspect
import re
import sys
import traceback
from typing import List, Dict, Callable, Union, Any

from opto.trace.broadcast import recursive_conversion
from opto.trace.errors import ExecutionError, TraceMissingInputsError
from opto.trace.modules import Module
from opto.trace.nodes import GRAPH
from opto.trace.nodes import MessageNode, USED_NODES, Node, ParameterNode, ExceptionNode, node, get_op_name
from opto.trace.utils import contain


def bundle(
    description=None,
    traceable_code=False,
    _process_inputs=True,
    trainable=False,
    catch_execution_error=True,
    allow_external_dependencies=False,
    overwrite_python_recursion=False,
):
    """
    Wrap a function as a FunModule, which returns node objects.
    The input signature to the wrapped function stays the same. bundle can be used with other decorators so long as they are not named 'bundle'.
    """
    prev_f_locals = inspect.stack()[1].frame.f_locals
    def decorator(fun):
        fun_module= FunModule(
            fun=fun,
            description=description,
            traceable_code=traceable_code,
            _process_inputs=_process_inputs,
            trainable=trainable,
            catch_execution_error=catch_execution_error,
            allow_external_dependencies=allow_external_dependencies,
            overwrite_python_recursion=overwrite_python_recursion,
            _ldict=prev_f_locals,  # Get the locals of the calling function
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
        _process_inputs (bool): if True, the input is extracted from the container of nodes; if False, the inputs are passed directly to the underlying function.
        trainable (bool): if True, the block of code is treated as a variable in the optimization
        catch_execution_error (bool): if True, the operator catches the exception raised during the execution of the operator and return ExecutionError.
        allow_external_dependencies (bool): if True, the operator allows external dependencies to be used in the operator. Namely, not all nodes used to create the output are in the inputs. In this case, the extra dependencies are stored in the info dictionary with key 'extra_dependencies'.
        overwrite_python_recursion (bool): if True, the operator allows the python recursion behavior of calling the decorated function to be overwritten. When true, applying bundle on a recursive function, would be the same as calling the function directly. When False, the Python's oriignal recursion behavior of decorated functions is preserved.
        _ldict (dict): the local dictionary to execute the code block.

    """

    def __init__(
        self,
        fun: Callable,
        description: str = None,
        traceable_code: bool = False,
        _process_inputs: bool = True,
        trainable=False,
        catch_execution_error=True,
        allow_external_dependencies=False,
        overwrite_python_recursion=False,
        _ldict=None,
    ):

        assert _ldict is None or isinstance(_ldict, dict), "_ldict must be a dictionary. or None"
        self._ldict = {} if _ldict is None else _ldict.copy()


        assert callable(fun), "fun must be a callable."

        # Get the source code of the function, excluding the decorator line
        source, line_number  = self.get_source(fun)

        # Construct the info dictionary
        docstring = inspect.getdoc(fun)
        self.info = dict(
            fun=None,  # to be defined at run time
            fun_name=fun.__qualname__,
            doc=inspect.cleandoc(docstring) if docstring is not None else "",
            signature=inspect.signature(fun),
            source=source,
            output=None,
            external_dependencies=None,
            line_number=line_number,
            file=inspect.getfile(fun),
            error_comment=None,
            traceback=None
        )

        if description is None:
            # Generate the description from the function name and docstring.
            description = f"[{self.info['fun_name']}] {self.info['doc']}."
        assert len(get_op_name(description)) > 0

        self.traceable_code = traceable_code
        self._fun = fun
        self.description = description
        self._process_inputs = _process_inputs
        self.catch_execution_error = catch_execution_error
        self.allow_external_dependencies = allow_external_dependencies
        self.parameter = None
        self.overwrite_python_recursion = overwrite_python_recursion
        if trainable:
            # trainable code uses exec which has an effect of overwrite_python_recursion==True.
            self.overwrite_python_recursion = True
            # assert overwrite_python_recursion, "trainable requires overwrite_python_recursion to be True."

            signature_sr = re.search(r"\s*(def.*\"\"\")", source, re.DOTALL)
            if signature_sr is None:  # if there is no docstring just take the first line
                signature = re.search(r"\s*(def.*:)", source).group(1)
            else:
                signature = signature_sr.group(1)
            self.parameter = ParameterNode(
                self.info["source"], name="__code", constraint="The code should start with:\n" + signature
            )

    @property
    def trainable(self):
        return self.parameter is not None

    @property
    def fun(self, *args, **kwargs):
        # This is called within trace_nodes context manager.
        if self.parameter is None:
            return self._fun
        else:
            code = self.parameter._data  # This is not traced, but we will add this as the parent later.
            # before we execute,  we should try to import all the global name spaces from the original function
            try:
                _ldict = {}
                gdict = self._fun.__globals__.copy()
                gdict.update(self._ldict)
                exec(code, gdict, _ldict)  # define the function
                fun_name = re.search(r"\s*def\s+(\w+)", code).group(1)
                fun = _ldict[fun_name]
                fun.__globals__[fun_name] = fun  # for recursive calls
            except SyntaxError as err:
                error_class = err.__class__.__name__
                detail = err.args[0]
                line_number = err.lineno
                e = err
            except Exception as err:
                # TODO would this ever happen?
                error_class = err.__class__.__name__
                detail = err.args[0]
                cl, exc, tb = sys.exc_info()
                line_number = traceback.extract_tb(tb)[-1][1]
                e = err
            else:
                return fun

            base_message = f'({error_class}) {detail}.'
            commented_code = self.generate_comment(code, base_message, line_number, 1) + f"\n{base_message}"
            raw_traceback = 'SyntaxError in trainable code definition.\n'  + commented_code if 'SyntaxError' == error_class else traceback.format_exc()
            self.info['error_comment'] = commented_code
            self.info['traceback'] = raw_traceback  # This is saved for user debugging

            e_node = ExceptionNode(
                e,
                inputs={"code": self.parameter},
                description=f"[exception] The code parameter {self.parameter.py_name} has an error.",
                name="exception_" + self.parameter.py_name,
                info=self.info,
            )

            raise ExecutionError(e_node)

    @property
    def name(self):
        return get_op_name(self.description)

    def forward(self, *args, **kwargs):
        """
        All nodes used in the operator fun are added to used_nodes during
        the execution. If the output is not a Node, we wrap it as a
        MessageNode, whose inputs are nodes in used_nodes.
        """


        fun = self.fun # define the function only once
        self.info['fun'] = fun

        ## Wrap the inputs as nodes

        # add default into kwargs
        ba = inspect.signature(fun).bind(*args, **kwargs)
        a0 = ba.arguments.copy()
        ba.apply_defaults()
        a1 = ba.arguments
        fullargspec = inspect.getfullargspec(fun)
        # include default into the kwargs
        for k,v in a1.items():
            if k not in a0:
                if k != fullargspec.varargs and k != fullargspec.varkw:
                    kwargs[k] = v
        # convert args and kwargs to nodes, except for FunModule
        _args, _kwargs = args, kwargs  # back up
        args = [node(a) if not isinstance(a, FunModule) else a for a in args ]
        kwargs = {k: node(v) if not isinstance(v, FunModule) else  v for k, v in kwargs.items() }

        ## Construct the input dict of the MessageNode from function inputs
        inputs = {}
        # args, varargs, varkw, defaults, kwonlyargs, kwonlydefaults, ann
        _, varargs, varkw, _, _, _, _ = inspect.getfullargspec(fun)


         # bind the node version of args and kwargs
        ba = inspect.signature(fun).bind(*args, **kwargs)
        spec = ba.arguments

        def extract_param(n):
            return n.parameter if isinstance(n, FunModule) and n.parameter is not None else n

        # expand varargs and varkw
        for k, v in spec.items():
            if k == varargs:  # unpack varargs
                for i, n in enumerate(v):
                    inputs[f"args_{i}"] = extract_param(n)  # TODO different representation?
            elif k == varkw:  # unpack varkw
                for kk, n in v.items():
                    inputs[kk] = extract_param(n)
            else:
                inputs[k] = extract_param(v)
        assert all([isinstance(n, Node) for n in inputs.values()]), "All values in inputs must be nodes."



        # Define a tracer to deal with recursive function calls
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

            # args, kwargs are nodes
            # _args, _kwargs are the original inputs (_kwargs inlcudes the defaults)

            # Construct the inputs to call self.fun
            if self._process_inputs:
                if self.traceable_code:
                    _args, _kwargs = detach_inputs(args), detach_inputs(kwargs)
                else:
                    _args, _kwargs = to_data(args), to_data(kwargs)
            # else the inputs are passed directly to the function
            # so we don't change _args and _kwargs

            oldtracer = sys.gettrace()
            if self.overwrite_python_recursion and self.parameter is None:  # Overwrite the python recursion behavior
                sys.settrace(tracer)
            # add an except here
            if self.catch_execution_error:
                try:
                    outputs = fun(*_args, **_kwargs)
                except Exception as e:
                    # Construct the error comment on the source code and traceback
                    self.info['traceback'] = traceback.format_exc()  # This is saved for user debugging
                    # Construct message to optimizer
                    error_class = e.__class__.__name__
                    detail = e.args[0]
                    cl, exc, tb = sys.exc_info()
                    n_fun_calls = len(traceback.extract_tb(tb))
                    # Step through the traceback stack
                    comments = []
                    base_message = f'({error_class}) {detail}.'
                    for i, (f, ln) in enumerate(traceback.walk_tb(tb)):
                        if i>0:  # ignore the first one, since that is the try statement above
                            error_message = base_message if i == n_fun_calls-1 else 'Error raised in function call. See below.'

                            if i==1 and self.parameter is not None:  # this is the trainable function defined by exec, which needs special treatment. inspect.getsource doesn't work here.
                                comment = self.generate_comment(self.parameter._data, error_message, ln, 1)
                                comment_backup = self.generate_comment(self.parameter._data, base_message, ln, 1)
                            else:
                                try:
                                    f_source, f_source_ln = self.get_source(f)
                                except OSError:  #  OSError: could not get source code
                                    # we reach the compiled C level, so the previous level is actually the bottom
                                    comments[-1] = comment_backup  # replace the previous comment
                                    break  # exit the loop
                                comment = self.generate_comment(f_source, error_message, ln, f_source_ln)
                                comment_backup = self.generate_comment(f_source, base_message, ln, f_source_ln)
                            comments.append(comment)
                    commented_code = '\n\n'.join(comments)
                    self.info['error_comment'] = commented_code + f"\n{base_message}"
                    outputs = e
            else:
                outputs = fun(*_args, **_kwargs)
            sys.settrace(oldtracer)


        # Nodes used to create the outputs but not in the inputs are external dependencies.
        external_dependencies = [node for node in used_nodes if not contain(inputs.values(), node)]
        self.info["external_dependencies"] = external_dependencies

        # Make sure all nodes in used_nodes are in the parents of the returned node.
        if len(external_dependencies) > 0 and not self.allow_external_dependencies:
            raise TraceMissingInputsError(
                f"Not all nodes used in the operator {fun} are specified as inputs of the returned node. Missing {[(node.name, node.data) for node in external_dependencies]} "
            )

        if not GRAPH.TRACE:
            inputs = {}  # We don't need to keep track of the inputs if we are not tracing.
        # Wrap the output as a MessageNode or an ExceptionNode
        nodes = self.wrap(outputs, inputs, external_dependencies)
        return nodes

    def wrap(self, output: Any, inputs: Union[List[Node], Dict[str, Node]], external_dependencies: List[Node]):
        """Wrap the output as a MessageNode of inputs as the parents."""
        # Some nodes are used in the operator fun, we need to wrap the output as a MessageNode.
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
            if isinstance(output, MessageNode):
                info["output"].info['inputs'] = list(inputs.values())
            return MessageNode(output, description=description, inputs=inputs, name=name, info=info)

    @staticmethod
    def is_valid_output(output):
        return isinstance(output, Node) or (isinstance(output, tuple) and all([isinstance(o, Node) for o in output]))

    def __get__(self, obj, objtype):
        # Support instance methods.
        return functools.partial(self.__call__, obj)

    def detach(self):
        return copy.deepcopy(self)

    def generate_comment(self, code: str, comment: str, comment_line_number: int, base_line_number: int = 0):
        commented_code = []
        for i, l in enumerate(code.split('\n')):
            if i == comment_line_number - base_line_number:
                commented_code.append(f"{l} <--- {comment}")
            else:
                commented_code.append(f"{l}")
        commented_code = '\n'.join(commented_code)
        return commented_code

    def get_source(self, obj: Any):
        """ Get the source code of the function and its line number, excluding the @bundle decorator line.

        Allowable two types of usages:

        Decorator style:

            @blah
            ...
            @bundle    # or  @ ....bundle()
            ...
            def fun(...): # ...
                ....


        or inline usage

            bundle()(fun)  # or ....bundle()(fun)

        """
        source = inspect.getsource(obj)  # the source includes @bundle, or @trace.bundle, etc. we will remove those parts.
        line_number = int(inspect.getsourcelines(obj)[1])  # line number of obj

        # Check if it's a decorator or an inline usage.
        decorator_usage = False
        lines = source.split('\n')
        for i, l in enumerate(lines):
            l = l.strip().split('#')[0]  # remove spacing and comment
            if l == '':
                continue
            if l[0] == '@':  # decorator line. check whether it's using bundle
                # use cases
                # @bundle(
                # @bundle\   i.e., change line
                # @......bundle(
                # @......bundle\
                if ('@bundle(' in l) or ('@bundle\\' in l) or \
                    (re.search(r'@.*\.bundle\(.*', l) is not None) or \
                    (re.search(r'@.*\.bundle\\.*', l) is not None):
                    decorator_usage = True
                    break  # i is the where the bundle decorator is used


        if decorator_usage:
            line_offset = i  # to account for @bundle is not the top decorator

            # Extract the lines after @bundle(....)
            inner_source = '\n'.join(lines[i:])  # i is where @bundle is used
            assert 'def ' in inner_source
            # str after the first bundle
            after_bundle = 'bundle'.join(inner_source.split('bundle')[1:])  # NOTE there may be multiple usages of bundle in the comments

            # Find where the scope of brackets
            count = 0
            for i, t in enumerate(after_bundle):
                if t == '(':
                    count += 1
                elif t == ')':
                    count -= 1
                if count == 0:
                    break
            # Get the decorated source code
            after_bundle_call = after_bundle[i+1:]  # after bundle(....)
            extracted_source = '\n'.join(after_bundle_call.split('\n')[1:])  # remove the first \n
            extracted_source = extracted_source.strip()
            # Get the line number of the decorated source code
            within_bundle_call = after_bundle[:i+1]
            n_line_changes = line_offset + 1 + within_bundle_call.count('\n')  # the latter is the lines within the bundle call
            line_number += n_line_changes
        else:
            # The inline usecase of
            # fun = @bundle(...)fun(...)
            #   ...
            extracted_source = inspect.getsource(obj).strip()


        assert 'def' in extracted_source, 'def must be in the source code'

        return extracted_source, line_number


def to_data(obj):
    """Extract the data from a node or a container of nodes."""
    return recursive_conversion(lambda x: x.data, lambda x: x)(obj)

def wrap_node(obj):
    """Wrap a node on top of the original object"""
    return recursive_conversion(lambda x: x, lambda x: node(x))(obj)

def detach_inputs(obj):
    """Detach a node or a container of nodes."""
    return recursive_conversion(lambda x: x.detach(), lambda x: x)(obj)


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