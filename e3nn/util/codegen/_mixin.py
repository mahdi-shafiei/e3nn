import io
import pickle
from typing import Dict

import e3nn.util.jit
import torch
from torch import fx


class CodeGenMixin:
    """Mixin for classes that dynamically generate TorchScript code using FX.

    This class manages evaluating and compiling generated code for subclasses
    while remaining pickle/deepcopy compatible. If subclasses need to override
    ``__getstate__``/``__setstate__``, they should be sure to call CodeGenMixin's
    implimentation first and use its output.
    """

    # pylint: disable=super-with-arguments

    def _codegen_register(
        self,
        funcs: Dict[str, fx.GraphModule],
    ) -> None:
        """Register ``fx.GraphModule``s as TorchScript submodules.

        Parameters
        ----------
            funcs : Dict[str, fx.GraphModule]
                Dictionary mapping submodule names to graph modules.
        """
        if not hasattr(self, "__codegen__"):
            # list of submodule names that are managed by this object
            self.__codegen__ = []
        self.__codegen__.extend(funcs.keys())

        opt_defaults = e3nn.get_optimization_defaults()

        for fname, graphmod in funcs.items():
            assert isinstance(graphmod, fx.GraphModule)

            if opt_defaults["jit_mode"] == "script":
                # With recurse=False, this more or less is equivalent to
                # torch.jit.script(jitable(graphmod))
                scriptmod = e3nn.util.jit.compile(graphmod, recurse=False)
                assert isinstance(scriptmod, torch.jit.ScriptModule)
            else:
                scriptmod = graphmod

            # Add the ScriptModule as a submodule so it can be called
            self.add_module(fname, scriptmod)

    # In order to support copy.deepcopy and pickling, we need to not save the compiled TorchScript functions:
    # See pickle docs: https://docs.python.org/3/library/pickle.html#pickling-class-instances
    def __getstate__(self):
        # - Get a state to work with -
        # We need to check if other parent classes of self define __getstate__
        # torch.nn.Module does not currently impliment __get/setstate__ but
        # may in the future, which is why we have these hasattr checks for
        # other superclasses.
        if hasattr(super(CodeGenMixin, self), "__getstate__"):
            out = super(CodeGenMixin, self).__getstate__()
        else:
            out = self.__dict__

        out = out.copy()
        # We need a copy of the _modules OrderedDict
        # Otherwise, modifying the returned state will modify the current module itself
        out["_modules"] = out["_modules"].copy()

        # - Add saved versions of the ScriptModules to the state -
        codegen_state = {}
        if hasattr(self, "__codegen__"):
            for fname in self.__codegen__:
                # Get the module
                smod = getattr(self, fname)
                buffer_type: str
                buffer: bytes
                if isinstance(smod, (fx.GraphModule, torch._dynamo.OptimizedModule)):
                    buffer_type = "fx"
                    # pickle the fx.GraphModule normally
                    buffer = pickle.dumps(smod)
                elif isinstance(smod, torch.jit.ScriptModule):
                    buffer_type = "torchscript"
                    # Save the compiled code as TorchScript IR
                    buffer_io = io.BytesIO()
                    torch.jit.save(smod, buffer_io)
                    # Serialize that IR (just some `bytes`) instead of
                    # the ScriptModule
                    buffer = buffer_io.getvalue()
                else:
                    assert False
                # Save the buffer and a note on what it is so we know how to load it
                codegen_state[fname] = (buffer_type, buffer)
                # Remove the compiled submodule from being a submodule
                # of the saved module
                del out["_modules"][fname]

            out["__codegen__"] = codegen_state
        return out

    def __setstate__(self, d) -> None:
        d = d.copy()
        # We don't want to add this to the object when we call super's __setstate__
        codegen_state = d.pop("__codegen__", None)

        # We need to initialize self first so that we can add submodules
        # We need to check if other parent classes of self define __getstate__
        if hasattr(super(CodeGenMixin, self), "__setstate__"):
            super(CodeGenMixin, self).__setstate__(d)
        else:
            self.__dict__.update(d)

        if codegen_state is not None:
            for fname, (buffer_type, buffer) in codegen_state.items():
                assert isinstance(fname, str)
                assert isinstance(buffer_type, str)
                # Make sure bytes, not ScriptModules, got made
                assert isinstance(buffer, bytes)
                if buffer_type == "fx":
                    smod = pickle.loads(buffer)
                    assert isinstance(smod, (fx.GraphModule, torch._dynamo.OptimizedModule))
                elif buffer_type == "torchscript":
                    # Load the TorchScript IR buffer
                    buffer = io.BytesIO(buffer)
                    smod = torch.jit.load(buffer)
                    assert isinstance(smod, torch.jit.ScriptModule)
                else:
                    raise NotImplementedError

                # Add the ScriptModule as a submodule
                setattr(self, fname, smod)
            self.__codegen__ = list(codegen_state.keys())
