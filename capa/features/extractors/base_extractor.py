# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import abc
import hashlib
import dataclasses
from copy import copy
from types import MethodType
from typing import Any, Union, Iterator, TypeAlias
from dataclasses import dataclass

import capa.features.address
from capa.features.common import Feature
from capa.features.address import Address, ThreadAddress, ProcessAddress, DynamicCallAddress, AbsoluteVirtualAddress

# feature extractors may reference functions, BBs, insns by opaque handle values.
# you can use the `.address` property to get and render the address of the feature.
#
# these handles are only consumed by routines on
# the feature extractor from which they were created.


@dataclass
class SampleHashes:
    md5: str
    sha1: str
    sha256: str

    @classmethod
    def from_bytes(cls, buf: bytes) -> "SampleHashes":
        md5 = hashlib.md5()
        sha1 = hashlib.sha1()
        sha256 = hashlib.sha256()
        md5.update(buf)
        sha1.update(buf)
        sha256.update(buf)

        return cls(md5=md5.hexdigest(), sha1=sha1.hexdigest(), sha256=sha256.hexdigest())


@dataclass
class FunctionHandle:
    """reference to a function recognized by a feature extractor.

    Attributes:
        address: the address of the function.
        inner: extractor-specific data.
        ctx: a context object for the extractor.
    """

    address: Address
    inner: Any
    ctx: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclass
class BBHandle:
    """reference to a basic block recognized by a feature extractor.

    Attributes:
        address: the address of the basic block start address.
        inner: extractor-specific data.
    """

    address: Address
    inner: Any


@dataclass
class InsnHandle:
    """reference to an instruction recognized by a feature extractor.

    Attributes:
        address: the address of the instruction address.
        inner: extractor-specific data.
    """

    address: Address
    inner: Any


class StaticFeatureExtractor:
    """
    StaticFeatureExtractor defines the interface for fetching features from a
    sample without running it; extractors that rely on the execution trace of
    a sample must implement the other sibling class, DynamicFeatureExtracor.

    There may be multiple backends that support fetching features for capa.
    For example, we use vivisect by default, but also want to support saving
     and restoring features from a JSON file.
    When we restore the features, we'd like to use exactly the same matching logic
     to find matching rules.
    Therefore, we can define a StaticFeatureExtractor that provides features from the
     serialized JSON file and do matching without a binary analysis pass.
    Also, this provides a way to hook in an IDA backend.

    This class is not instantiated directly; it is the base class for other implementations.
    """

    __metaclass__ = abc.ABCMeta

    def __init__(self, hashes: SampleHashes):
        #
        # note: a subclass should define ctor parameters for its own use.
        #  for example, the Vivisect feature extract might require the vw and/or path.
        # this base class doesn't know what to do with that info, though.
        #
        super().__init__()
        self._sample_hashes = hashes

    @abc.abstractmethod
    def get_base_address(self) -> Union[AbsoluteVirtualAddress, capa.features.address._NoAddress]:
        """
        fetch the preferred load address at which the sample was analyzed.

        when the base address is `NO_ADDRESS`, then the loader has no concept of a preferred load address.
        such as: shellcode, .NET modules, etc.
        in these scenarios, RelativeVirtualAddresses aren't used.
        """
        raise NotImplementedError()

    def get_sample_hashes(self) -> SampleHashes:
        """
        fetch the hashes for the sample contained within the extractor.
        """
        return self._sample_hashes

    @abc.abstractmethod
    def extract_global_features(self) -> Iterator[tuple[Feature, Address]]:
        """
        extract features found at every scope ("global").

        example::

            extractor = VivisectFeatureExtractor(vw, path)
            for feature, va in extractor.get_global_features():
                print('0x%x: %s', va, feature)

        yields:
          tuple[Feature, Address]: feature and its location
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def extract_file_features(self) -> Iterator[tuple[Feature, Address]]:
        """
        extract file-scope features.

        example::

            extractor = VivisectFeatureExtractor(vw, path)
            for feature, va in extractor.get_file_features():
                print('0x%x: %s', va, feature)

        yields:
          tuple[Feature, Address]: feature and its location
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_functions(self) -> Iterator[FunctionHandle]:
        """
        enumerate the functions and provide opaque values that will
         subsequently be provided to `.extract_function_features()`, etc.
        """
        raise NotImplementedError()

    def is_library_function(self, addr: Address) -> bool:
        """
        is the given address a library function?
        the backend may implement its own function matching algorithm, or none at all.
        we accept an address here, rather than function object,
         to handle addresses identified in instructions.

        this information is used to:
          - filter out matches in library functions (by default), and
          - recognize when to fetch symbol names for called (non-API) functions

        args:
          addr (Address): the address of a function.

        returns:
          bool: True if the given address is the start of a library function.
        """
        return False

    def get_function_name(self, addr: Address) -> str:
        """
        fetch any recognized name for the given address.
        this is only guaranteed to return a value when the given function is a recognized library function.
        we accept a VA here, rather than function object, to handle addresses identified in instructions.

        args:
          addr (Address): the address of a function.

        returns:
          str: the function name

        raises:
          KeyError: when the given function does not have a name.
        """
        raise KeyError(addr)

    @abc.abstractmethod
    def extract_function_features(self, f: FunctionHandle) -> Iterator[tuple[Feature, Address]]:
        """
        extract function-scope features.
        the arguments are opaque values previously provided by `.get_functions()`, etc.

        example::

            extractor = VivisectFeatureExtractor(vw, path)
            for function in extractor.get_functions():
                for feature, address in extractor.extract_function_features(function):
                    print('0x%x: %s', address, feature)

        args:
          f [FunctionHandle]: an opaque value previously fetched from `.get_functions()`.

        yields:
          tuple[Feature, Address]: feature and its location
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_basic_blocks(self, f: FunctionHandle) -> Iterator[BBHandle]:
        """
        enumerate the basic blocks in the given function and provide opaque values that will
         subsequently be provided to `.extract_basic_block_features()`, etc.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def extract_basic_block_features(self, f: FunctionHandle, bb: BBHandle) -> Iterator[tuple[Feature, Address]]:
        """
        extract basic block-scope features.
        the arguments are opaque values previously provided by `.get_functions()`, etc.

        example::

            extractor = VivisectFeatureExtractor(vw, path)
            for function in extractor.get_functions():
                for bb in extractor.get_basic_blocks(function):
                    for feature, address in extractor.extract_basic_block_features(function, bb):
                        print('0x%x: %s', address, feature)

        args:
          f [FunctionHandle]: an opaque value previously fetched from `.get_functions()`.
          bb [BBHandle]: an opaque value previously fetched from `.get_basic_blocks()`.

        yields:
          tuple[Feature, Address]: feature and its location
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_instructions(self, f: FunctionHandle, bb: BBHandle) -> Iterator[InsnHandle]:
        """
        enumerate the instructions in the given basic block and provide opaque values that will
         subsequently be provided to `.extract_insn_features()`, etc.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def extract_insn_features(
        self, f: FunctionHandle, bb: BBHandle, insn: InsnHandle
    ) -> Iterator[tuple[Feature, Address]]:
        """
        extract instruction-scope features.
        the arguments are opaque values previously provided by `.get_functions()`, etc.

        example::

            extractor = VivisectFeatureExtractor(vw, path)
            for function in extractor.get_functions():
                for bb in extractor.get_basic_blocks(function):
                    for insn in extractor.get_instructions(function, bb):
                        for feature, address in extractor.extract_insn_features(function, bb, insn):
                            print('0x%x: %s', address, feature)

        args:
          f [FunctionHandle]: an opaque value previously fetched from `.get_functions()`.
          bb [BBHandle]: an opaque value previously fetched from `.get_basic_blocks()`.
          insn [InsnHandle]: an opaque value previously fetched from `.get_instructions()`.

        yields:
          tuple[Feature, Address]: feature and its location
        """
        raise NotImplementedError()


def FunctionFilter(extractor: StaticFeatureExtractor, functions: set) -> StaticFeatureExtractor:
    original_get_functions = extractor.get_functions

    def filtered_get_functions(self):
        yield from (f for f in original_get_functions() if f.address in functions)

    # we make a copy of the original extractor object and then update its get_functions() method with the decorated filter one.
    # this is in order to preserve the original extractor object's get_functions() method, in case it is used elsewhere in the code.
    # an example where this is important is in our testfiles where we may use the same extractor object with different tests,
    # with some of these tests needing to install a functions filter on the extractor object.
    new_extractor = copy(extractor)
    new_extractor.get_functions = MethodType(filtered_get_functions, extractor)  # type: ignore

    return new_extractor


@dataclass
class ProcessHandle:
    """
    reference to a process extracted by the sandbox.

    Attributes:
        address: process's address (pid)
        inner: sandbox-specific data
    """

    address: ProcessAddress
    inner: Any


@dataclass
class ThreadHandle:
    """
    reference to a thread extracted by the sandbox.

    Attributes:
        address: thread's address (tid)
        inner: sandbox-specific data
    """

    address: ThreadAddress
    inner: Any


@dataclass
class CallHandle:
    """
    reference to an api call extracted by the sandbox.

    Attributes:
        address: call's address, such as event index or id
        inner: sandbox-specific data
    """

    address: DynamicCallAddress
    inner: Any


class DynamicFeatureExtractor:
    """
    DynamicFeatureExtractor defines the interface for fetching features from a
    sandbox' analysis of a sample; extractors that rely on statically analyzing
    a sample must implement the sibling extractor, StaticFeatureExtractor.

    Features are grouped mainly into threads that alongside their meta-features are also grouped into
    processes (that also have their own features). Other scopes (such as function and file) may also apply
    for a specific sandbox.

    This class is not instantiated directly; it is the base class for other implementations.
    """

    __metaclass__ = abc.ABCMeta

    def __init__(self, hashes: SampleHashes):
        #
        # note: a subclass should define ctor parameters for its own use.
        #  for example, the Vivisect feature extract might require the vw and/or path.
        # this base class doesn't know what to do with that info, though.
        #
        super().__init__()
        self._sample_hashes = hashes

    def get_sample_hashes(self) -> SampleHashes:
        """
        fetch the hashes for the sample contained within the extractor.
        """
        return self._sample_hashes

    @abc.abstractmethod
    def extract_global_features(self) -> Iterator[tuple[Feature, Address]]:
        """
        extract features found at every scope ("global").

        example::

            extractor = CapeFeatureExtractor.from_report(json.loads(buf))
            for feature, addr in extractor.get_global_features():
                print(addr, feature)

        yields:
          tuple[Feature, Address]: feature and its location
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def extract_file_features(self) -> Iterator[tuple[Feature, Address]]:
        """
        extract file-scope features.

        example::

            extractor = CapeFeatureExtractor.from_report(json.loads(buf))
            for feature, addr in extractor.get_file_features():
                print(addr, feature)

        yields:
          tuple[Feature, Address]: feature and its location
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_processes(self) -> Iterator[ProcessHandle]:
        """
        Enumerate processes in the trace.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def extract_process_features(self, ph: ProcessHandle) -> Iterator[tuple[Feature, Address]]:
        """
        Yields all the features of a process. These include:
        - file features of the process' image
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_process_name(self, ph: ProcessHandle) -> str:
        """
        Returns the human-readable name for the given process,
        such as the filename.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_threads(self, ph: ProcessHandle) -> Iterator[ThreadHandle]:
        """
        Enumerate threads in the given process.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def extract_thread_features(self, ph: ProcessHandle, th: ThreadHandle) -> Iterator[tuple[Feature, Address]]:
        """
        Yields all the features of a thread. These include:
        - sequenced api traces
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_calls(self, ph: ProcessHandle, th: ThreadHandle) -> Iterator[CallHandle]:
        """
        Enumerate calls in the given thread
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def extract_call_features(
        self, ph: ProcessHandle, th: ThreadHandle, ch: CallHandle
    ) -> Iterator[tuple[Feature, Address]]:
        """
        Yields all features of a call. These include:
        - api name
        - bytes/strings/numbers extracted from arguments
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def get_call_name(self, ph: ProcessHandle, th: ThreadHandle, ch: CallHandle) -> str:
        """
        Returns the human-readable name for the given call,
        such as as rendered API log entry, like:

            Foo(1, "two", b"\x00\x11") -> -1
        """
        raise NotImplementedError()


def ProcessFilter(extractor: DynamicFeatureExtractor, pids: set[int]) -> DynamicFeatureExtractor:
    original_get_processes = extractor.get_processes

    def filtered_get_processes(self):
        yield from (f for f in original_get_processes() if f.address.pid in pids)

    # we make a copy of the original extractor object and then update its get_processes() method with the decorated filter one.
    # this is in order to preserve the original extractor object's get_processes() method, in case it is used elsewhere in the code.
    # an example where this is important is in our testfiles where we may use the same extractor object with different tests,
    # with some of these tests needing to install a processes filter on the extractor object.
    new_extractor = copy(extractor)
    new_extractor.get_processes = MethodType(filtered_get_processes, extractor)  # type: ignore

    return new_extractor


def ThreadFilter(extractor: DynamicFeatureExtractor, threads: set[Address]) -> DynamicFeatureExtractor:
    original_get_threads = extractor.get_threads

    def filtered_get_threads(self, ph: ProcessHandle):
        yield from (t for t in original_get_threads(ph) if t.address in threads)

    new_extractor = copy(extractor)
    new_extractor.get_threads = MethodType(filtered_get_threads, extractor)  # type: ignore

    return new_extractor


FeatureExtractor: TypeAlias = Union[StaticFeatureExtractor, DynamicFeatureExtractor]
