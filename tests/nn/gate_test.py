import functools

import torch

from e3nn.o3 import Irreps
from e3nn.nn import Gate
from e3nn.nn._gate import _Sortcut
from e3nn.util.test import assert_equivariant, assert_auto_jitable, assert_normalized, assert_torch_compile


def test_gate() -> None:
    irreps_scalars, act_scalars, irreps_gates, act_gates, irreps_gated = (
        Irreps("16x0o"),
        [torch.tanh],
        Irreps("32x0o"),
        [torch.tanh],
        Irreps("16x1e+16x1o"),
    )

    sc = _Sortcut(irreps_scalars, irreps_gates)
    assert_auto_jitable(sc)

    g = Gate(irreps_scalars, act_scalars, irreps_gates, act_gates, irreps_gated)
    irreps = Irreps("16x0o+32x0o+16x1e+16x1o")

    assert_equivariant(g)
    assert_auto_jitable(g)
    assert_torch_compile(
        "inductor",
        functools.partial(Gate, irreps_scalars, act_scalars, irreps_gates, act_gates, irreps_gated),
        irreps.randn(-1),
    )
    assert_normalized(g)
