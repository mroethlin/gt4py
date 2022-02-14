# -*- coding: utf-8 -*-
#
# GT4Py Project - GridTools Framework
#
# Copyright (c) 2014-2021, ETH Zurich
# All rights reserved.
#
# This file is part of the GT4Py project and the GridTools framework.
# GT4Py is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or any later
# version. See the LICENSE.txt file at the top-level directory of this
# distribution for a copy of the license or check <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from typing import TypeVar

import numpy as np

from functional.ffront.fbuiltins import Field, float64, nbh_sum
from functional.ffront.foast_to_itir import FieldOperatorLowering
from functional.ffront.func_to_foast import FieldOperatorParser
from functional.iterator import ir as itir
from functional.iterator.backends import roundtrip
from functional.iterator.embedded import (
    np_as_located_field,
    NeighborTableOffsetProvider,
    index_field,
)
from functional.iterator.runtime import CartesianAxis, offset


def make_domain(dim_name: str, lower: int, upper: int) -> itir.FunCall:
    return itir.FunCall(
        fun=itir.SymRef(id="domain"),
        args=[
            itir.FunCall(
                fun=itir.SymRef(id="named_range"),
                args=[
                    itir.AxisLiteral(value=dim_name),
                    itir.IntLiteral(value=lower),
                    itir.IntLiteral(value=upper),
                ],
            )
        ],
    )


def closure_from_fop(
    node: itir.FunctionDefinition, out_names: list[str], domain: itir.FunCall
) -> itir.StencilClosure:
    return itir.StencilClosure(
        stencil=itir.SymRef(id=node.id),
        inputs=[itir.SymRef(id=sym.id) for sym in node.params],
        outputs=[itir.SymRef(id=name) for name in out_names],
        domain=domain,
    )


def fencil_from_fop(
    node: itir.FunctionDefinition, out_names: list[str], domain: itir.FunCall
) -> itir.FencilDefinition:
    closure = closure_from_fop(node, out_names=out_names, domain=domain)
    return itir.FencilDefinition(
        id=node.id + "_fencil",
        params=[itir.Sym(id=inp.id) for inp in closure.inputs]
        + [itir.Sym(id=out.id) for out in closure.outputs],
        closures=[closure],
    )


# todo(tehrengruber): dim and size are implicitly given bys out_names. Get values from there
def program_from_fop(
    node: itir.FunctionDefinition, out_names: list[str], dim: CartesianAxis, size: int
) -> itir.Program:
    domain = make_domain(dim.value, 0, size)
    return itir.Program(
        function_definitions=[node],
        fencil_definitions=[fencil_from_fop(node, out_names=out_names, domain=domain)],
        setqs=[],
    )


# todo(tehrengruber): dim and size are implicitly given bys out_names. Get values from there
def program_from_function(
    func, out_names: list[str], dim: CartesianAxis, size: int
) -> itir.Program:
    return program_from_fop(
        node=FieldOperatorLowering.apply(FieldOperatorParser.apply_to_function(func)),
        out_names=out_names,
        dim=dim,
        size=size,
    )


DimsType = TypeVar("DimsType")
DType = TypeVar("DType")

IDim = CartesianAxis("IDim")


def test_copy():
    size = 10
    a = np_as_located_field(IDim)(np.ones((size)))
    b = np_as_located_field(IDim)(np.zeros((size)))

    def copy(inp: Field[[IDim], float64]):
        return inp

    program = program_from_function(copy, out_names=["out"], dim=IDim, size=size)

    roundtrip.executor(program, a, b, offset_provider={})

    assert np.allclose(a, b)


def test_multicopy():
    size = 10
    a = np_as_located_field(IDim)(np.ones((size)))
    b = np_as_located_field(IDim)(np.ones((size)) * 3)
    c = np_as_located_field(IDim)(np.zeros((size)))
    d = np_as_located_field(IDim)(np.zeros((size)))

    def multicopy(inp1: Field[[IDim], float64], inp2: Field[[IDim], float64]):
        return inp1, inp2

    program = program_from_function(multicopy, out_names=["c", "d"], dim=IDim, size=size)
    roundtrip.executor(program, a, b, c, d, offset_provider={})

    assert np.allclose(a, c)
    assert np.allclose(b, d)


def test_arithmetic():
    size = 10
    a = np_as_located_field(IDim)(np.ones((size)))
    b = np_as_located_field(IDim)(np.ones((size)) * 2)
    c = np_as_located_field(IDim)(np.zeros((size)))

    def arithmetic(inp1: Field[[IDim], float64], inp2: Field[[IDim], float64]):
        return inp1 + inp2

    program = program_from_function(arithmetic, out_names=["c"], dim=IDim, size=size)
    roundtrip.executor(program, a, b, c, offset_provider={})

    assert np.allclose(a.array() + b.array(), c)


def test_bit_logic():
    size = 10
    a = np_as_located_field(IDim)(np.full((size), True))
    b_data = np.full((size), True)
    b_data[5] = False
    b = np_as_located_field(IDim)(b_data)
    c = np_as_located_field(IDim)(np.full((size), False))

    def bit_and(inp1: Field[[IDim], bool], inp2: Field[[IDim], bool]):
        return inp1 & inp2

    program = program_from_function(bit_and, out_names=["c"], dim=IDim, size=size)
    roundtrip.executor(program, a, b, c, offset_provider={})

    assert np.allclose(a.array() & b.array(), c)


def test_unary_neg():
    size = 10
    a = np_as_located_field(IDim)(np.ones((size)))
    b = np_as_located_field(IDim)(np.zeros((size)))

    def uneg(inp: Field[[IDim], int]):
        return -inp

    program = program_from_function(uneg, out_names=["b"], dim=IDim, size=size)
    roundtrip.executor(program, a, b, offset_provider={})

    assert np.allclose(b, np.full((size), -1))


def test_shift():
    size = 10
    Ioff = offset("Ioff", source=IDim, target=[IDim, IDim])
    a = np_as_located_field(IDim)(np.arange(size + 1))
    b = np_as_located_field(IDim)(np.zeros((size)))

    def shift_by_one(inp: Field[[IDim], float64]):
        return inp(Ioff[1])

    program = program_from_function(shift_by_one, out_names=["b"], dim=IDim, size=size)
    roundtrip.executor(program, a, b, offset_provider={"Ioff": IDim})

    assert np.allclose(b.array(), np.arange(1, 11))


def test_fold_shifts():
    """Shifting the result of an addition should work by shifting the operands instead."""
    size = 10
    Ioff = offset("Ioff", source=IDim, target=[IDim, IDim])
    a = np_as_located_field(IDim)(np.arange(size + 1))
    b = np_as_located_field(IDim)(np.ones((size + 1)) * 2)
    c = np_as_located_field(IDim)(np.zeros((size)))

    def auto_lift(inp1: Field[[IDim], float64], inp2: Field[[IDim], float64]):
        tmp = inp1 + inp2
        return tmp(Ioff[1])

    program = program_from_function(auto_lift, out_names=["c"], dim=IDim, size=size)
    roundtrip.executor(program, a, b, c, offset_provider={"Ioff": IDim})

    assert np.allclose(a[1:] + b[1:], c)


def test_reduction_execution():
    """Testing a trivial neighbor sum"""
    size = 9

    Edge = CartesianAxis("Edge")
    Vertex = CartesianAxis("Vertex")
    V2EDim = CartesianAxis("V2E")
    V2E = offset("V2E", source=Edge, target=(Vertex, V2EDim))

    v2e_arr = np.array(
        [
            [0, 15, 2, 9],  # 0
            [1, 16, 0, 10],
            [2, 17, 1, 11],
            [3, 9, 5, 12],  # 3
            [4, 10, 3, 13],
            [5, 11, 4, 14],
            [6, 12, 8, 15],  # 6
            [7, 13, 6, 16],
            [8, 14, 7, 17],
        ]
    )

    inp = index_field(Edge)
    out = np_as_located_field(Vertex)(np.zeros([9]))
    ref = np.asarray(list(sum(row) for row in v2e_arr))

    def reduction(edge_f: Field[[Edge], "float64"]):
        return nbh_sum(edge_f(V2E), axis=V2EDim)

    program = program_from_function(reduction, out_names=["out"], dim=Vertex, size=size)
    roundtrip.executor(
        program,
        inp,
        out,
        offset_provider={"V2E": NeighborTableOffsetProvider(v2e_arr, Vertex, Edge, 4)},
    )

    assert np.allclose(ref, out)
