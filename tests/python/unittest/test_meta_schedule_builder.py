# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
""" Test Meta Schedule Builder """

import os
import sys
import time
from typing import List

import pytest

from tvm import tir, script
from tvm._ffi import register_func
from tvm.meta_schedule.builder import (
    BuilderInput,
    BuilderResult,
    LocalBuilder,
    PyBuilder,
)
from tvm.runtime import Module
from tvm.script import ty
from tvm.target import Target


# pylint: disable=invalid-name,no-member,line-too-long,too-many-nested-blocks,missing-docstring


@script.tir
class MatmulModule:
    def matmul(  # pylint: disable=no-self-argument
        a: ty.handle, b: ty.handle, c: ty.handle
    ) -> None:
        tir.func_attr({"global_symbol": "matmul", "tir.noalias": True})
        A = tir.match_buffer(a, (1024, 1024), "float32")
        B = tir.match_buffer(b, (1024, 1024), "float32")
        C = tir.match_buffer(c, (1024, 1024), "float32")
        with tir.block([1024, 1024, tir.reduce_axis(0, 1024)], "matmul") as [vi, vj, vk]:
            with tir.init():
                C[vi, vj] = 0.0
            C[vi, vj] = C[vi, vj] + A[vi, vk] * B[vk, vj]


@script.tir
class MatmulReluModule:
    def matmul_relu(  # pylint: disable=no-self-argument
        a: ty.handle, b: ty.handle, d: ty.handle
    ) -> None:
        tir.func_attr({"global_symbol": "matmul_relu", "tir.noalias": True})
        A = tir.match_buffer(a, (1024, 1024), "float32")
        B = tir.match_buffer(b, (1024, 1024), "float32")
        D = tir.match_buffer(d, (1024, 1024), "float32")
        C = tir.alloc_buffer((1024, 1024), "float32")
        with tir.block([1024, 1024, tir.reduce_axis(0, 1024)], "matmul") as [vi, vj, vk]:
            with tir.init():
                C[vi, vj] = 0.0
            C[vi, vj] = C[vi, vj] + A[vi, vk] * B[vk, vj]
        with tir.block([1024, 1024], "relu") as [vi, vj]:
            D[vi, vj] = tir.max(C[vi, vj], 0.0)


@script.tir
class BatchMatmulModule:
    def batch_matmul(  # pylint: disable=no-self-argument
        a: ty.handle, b: ty.handle, c: ty.handle
    ) -> None:
        tir.func_attr({"global_symbol": "batch_matmul", "tir.noalias": True})
        A = tir.match_buffer(a, [16, 128, 128])
        B = tir.match_buffer(b, [16, 128, 128])
        C = tir.match_buffer(c, [16, 128, 128])
        with tir.block([16, 128, 128, tir.reduce_axis(0, 128)], "update") as [vn, vi, vj, vk]:
            with tir.init():
                C[vn, vi, vj] = 0.0
            C[vn, vi, vj] = C[vn, vi, vj] + A[vn, vi, vk] * B[vn, vj, vk]


# pylint: enable=invalid-name,no-member,line-too-long,too-many-nested-blocks,missing-docstring


def _check_build_results(builder_results: List[BuilderResult]):
    """Simple check whether the build is successful"""
    for result in builder_results:
        artifact_path = result.artifact_path
        error_msg = result.error_msg
        assert artifact_path is not None
        assert error_msg is None
        os.remove(artifact_path)
        os.rmdir(os.path.dirname(artifact_path))


def test_meta_schedule_single_build():
    """Test meta schedule builder for a single build"""
    mod = MatmulModule()
    builder = LocalBuilder()
    builder_inputs = [BuilderInput(mod, Target("llvm"))]
    builder_results = builder.build(builder_inputs)
    assert len(builder_results) == len(builder_inputs)
    _check_build_results(builder_results)


def test_meta_schedule_multiple_build():
    """Test meta schedule builder for multiple builds"""
    builder = LocalBuilder()
    builder_inputs = [
        BuilderInput(MatmulModule(), Target("llvm")),
        BuilderInput(MatmulReluModule(), Target("llvm")),
        BuilderInput(BatchMatmulModule(), Target("llvm")),
    ]
    builder_results = builder.build(builder_inputs)
    assert len(builder_results) == len(builder_inputs)
    _check_build_results(builder_results)


def test_meta_schedule_error_handle_test_builder():
    """Test the error handing during building"""

    class TestBuilder(PyBuilder):
        def build(  # pylint: disable=no-self-use
            self,
            build_inputs: List[BuilderInput],
        ) -> List[BuilderResult]:
            return [BuilderResult(None, "error") for w in build_inputs]

    builder = TestBuilder()
    builder_inputs = [
        BuilderInput(MatmulModule(), Target("llvm")),
        BuilderInput(MatmulReluModule(), Target("llvm")),
        BuilderInput(BatchMatmulModule(), Target("llvm")),
    ]
    builder_results = builder.build(builder_inputs)
    assert len(builder_results) == len(builder_inputs)
    for result in builder_results:
        artifact_path = result.artifact_path
        error_msg = result.error_msg
        assert artifact_path is None
        assert error_msg == "error"


def test_meta_schedule_error_handle_build_func():
    """Test the error handing during building"""

    def initializer():
        @register_func("meta_schedule.builder.test_build")
        def test_build(mod: Module, target: Target) -> None:  # pylint: disable=unused-variable
            raise ValueError("Builder intended Test Error (build func).")

    builder = LocalBuilder(f_build="meta_schedule.builder.test_build", initializer=initializer)
    builder_inputs = [BuilderInput(MatmulModule(), Target("llvm"))]
    builder_results = builder.build(builder_inputs)
    assert len(builder_results) == len(builder_inputs)
    for result in builder_results:
        artifact_path = result.artifact_path
        error_msg = result.error_msg
        assert artifact_path is None
        assert error_msg.startswith("LocalBuilder: An exception occurred")


def test_meta_schedule_error_handle_export_func():
    """Test the error handing during building"""

    def initializer():
        @register_func("meta_schedule.builder.test_export")
        def test_build(mod: Module) -> str:  # pylint: disable=unused-variable
            raise ValueError("Builder intended Test Error (export func).")

    builder = LocalBuilder(f_export="meta_schedule.builder.test_export", initializer=initializer)
    builder_inputs = [BuilderInput(MatmulModule(), Target("llvm"))]
    builder_results = builder.build(builder_inputs)
    assert len(builder_results) == len(builder_inputs)
    for result in builder_results:
        artifact_path = result.artifact_path
        error_msg = result.error_msg
        assert artifact_path is None
        assert error_msg.startswith("LocalBuilder: An exception occurred")


def test_meta_schedule_error_handle_time_out():
    """Test the error handing time out during building"""

    def initializer():
        @register_func("meta_schedule.builder.test_time_out")
        def timeout_build(mod, target):  # pylint: disable=unused-argument, unused-variable
            time.sleep(2)

    builder = LocalBuilder(
        timeout_sec=1,
        f_build="meta_schedule.builder.test_time_out",
        initializer=initializer,
    )
    builder_inputs = [BuilderInput(MatmulModule(), Target("llvm"))]
    builder_results = builder.build(builder_inputs)
    assert len(builder_results) == len(builder_inputs)
    for result in builder_results:
        artifact_path = result.artifact_path
        error_msg = result.error_msg
        assert artifact_path is None
        assert error_msg.startswith("LocalBuilder: Timeout")


def test_meta_schedule_missing_build_func():
    with pytest.raises(ValueError):
        LocalBuilder(f_build="wrong-name")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__] + sys.argv[1:]))
