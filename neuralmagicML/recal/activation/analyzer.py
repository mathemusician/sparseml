from typing import List, Union, Tuple
from enum import Enum
import torch
from torch import Tensor
from torch.nn import Module
from torch.utils.hooks import RemovableHandle

from ..helpers import tensor_sparsity, tensor_sample, get_layer


__all__ = ['ASResultType', 'ModuleASAnalyzer']


class ASResultType(Enum):
    inputs_sparsity = 'inputs_sparsity'
    inputs_sample = 'inputs_sample'
    outputs_sparsity = 'outputs_sparsity'
    outputs_sample = 'outputs_sample'


class ModuleASAnalyzer(object):
    @staticmethod
    def analyze_layers(module: Module, layers: List[str], **kwargs):
        analyzed = []

        for layer_name in layers:
            layer = get_layer(layer_name, module)
            analyzed.append(ModuleASAnalyzer(layer, **kwargs))

        return analyzed

    def __init__(self, module: Module, division: Union[None, int, Tuple[int, ...]],
                 track_inputs_sparsity: bool = False, track_outputs_sparsity: bool = False,
                 inputs_sample_size: int = 0, outputs_sample_size: int = 0,
                 enabled: bool = False):
        self._module = module
        self._division = division
        self._track_inputs_sparsity = track_inputs_sparsity
        self._track_outputs_sparsity = track_outputs_sparsity
        self._inputs_sample_size = inputs_sample_size
        self._outputs_sample_size = outputs_sample_size
        self._enabled = enabled

        self._inputs_sparsity = []  # type: List[Tensor]
        self._inputs_sample = []  # type: List[Tensor]
        self._outputs_sparsity = []  # type: List[Tensor]
        self._outputs_sample = []  # type: List[Tensor]
        self._pre_hook_handle = None  # type: RemovableHandle
        self._hook_handle = None  # type: RemovableHandle

        self.enable()

    def __del__(self):
        self._disable_hooks()

    def __str__(self):
        return ('module: {}, division: {}, track_inputs_sparsity: {}, track_outputs_sparsity: {}, '
                'inputs_sample_size: {}, outputs_sample_size: {}, enabled: {}'
                .format(self._module, self._division, self._track_inputs_sparsity, self._track_outputs_sparsity,
                        self._inputs_sample_size, self._outputs_sample_size, self._enabled))

    @property
    def module(self) -> Module:
        return self._module

    @property
    def division(self) -> Union[None, int, Tuple[int, ...]]:
        return self._division

    @property
    def track_inputs_sparsity(self) -> bool:
        return self._track_inputs_sparsity

    @track_inputs_sparsity.setter
    def track_inputs_sparsity(self, value: bool):
        self._track_inputs_sparsity = value

    @property
    def track_outputs_sparsity(self) -> bool:
        return self._track_outputs_sparsity

    @track_outputs_sparsity.setter
    def track_outputs_sparsity(self, value: bool):
        self._track_outputs_sparsity = value

    @property
    def inputs_sample_size(self) -> int:
        return self._inputs_sample_size

    @inputs_sample_size.setter
    def inputs_sample_size(self, value: int):
        self._inputs_sample_size = value

    @property
    def outputs_sample_size(self) -> int:
        return self._outputs_sample_size

    @outputs_sample_size.setter
    def outputs_sample_size(self, value: int):
        self._outputs_sample_size = value

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def inputs_sparsity(self) -> List[Tensor]:
        return self.results(ASResultType.inputs_sparsity)

    @property
    def inputs_sparsity_mean(self) -> Tensor:
        return self.results_mean(ASResultType.inputs_sparsity)

    @property
    def inputs_sparsity_std(self) -> Tensor:
        return self.results_std(ASResultType.inputs_sparsity)

    @property
    def inputs_sparsity_max(self) -> Tensor:
        return self.results_max(ASResultType.inputs_sparsity)

    @property
    def inputs_sparsity_min(self) -> Tensor:
        return self.results_min(ASResultType.inputs_sparsity)

    @property
    def inputs_sample(self) -> List[Tensor]:
        return self.results(ASResultType.inputs_sample)

    @property
    def inputs_sample_mean(self) -> Tensor:
        return self.results_mean(ASResultType.inputs_sample)

    @property
    def inputs_sample_std(self) -> Tensor:
        return self.results_std(ASResultType.inputs_sample)

    @property
    def inputs_sample_max(self) -> Tensor:
        return self.results_max(ASResultType.inputs_sample)

    @property
    def inputs_sample_min(self) -> Tensor:
        return self.results_min(ASResultType.inputs_sample)

    @property
    def outputs_sparsity(self) -> List[Tensor]:
        return self.results(ASResultType.outputs_sparsity)

    @property
    def outputs_sparsity_mean(self) -> Tensor:
        return self.results_mean(ASResultType.outputs_sparsity)

    @property
    def outputs_sparsity_std(self) -> Tensor:
        return self.results_std(ASResultType.outputs_sparsity)

    @property
    def outputs_sparsity_max(self) -> Tensor:
        return self.results_max(ASResultType.outputs_sparsity)

    @property
    def outputs_sparsity_min(self) -> Tensor:
        return self.results_min(ASResultType.outputs_sparsity)

    @property
    def outputs_sample(self) -> List[Tensor]:
        return self.results(ASResultType.outputs_sample)

    @property
    def outputs_sample_mean(self) -> Tensor:
        return self.results_mean(ASResultType.outputs_sample)

    @property
    def outputs_sample_std(self) -> Tensor:
        return self.results_std(ASResultType.outputs_sample)
    
    @property
    def outputs_sample_max(self) -> Tensor:
        return self.results_max(ASResultType.outputs_sample)
    
    @property
    def outputs_sample_min(self) -> Tensor:
        return self.results_min(ASResultType.outputs_sample)

    def clear(self, specific_result_type: Union[None, ASResultType] = None):
        if specific_result_type is None or specific_result_type == ASResultType.inputs_sparsity:
            self._inputs_sparsity.clear()

        if specific_result_type is None or specific_result_type == ASResultType.inputs_sample:
            self._inputs_sample.clear()

        if specific_result_type is None or specific_result_type == ASResultType.outputs_sparsity:
            self._outputs_sparsity.clear()

        if specific_result_type is None or specific_result_type == ASResultType.outputs_sample:
            self._outputs_sample.clear()

    def enable(self):
        if not self._enabled:
            self._enabled = True
            self._enable_hooks()

    def disable(self):
        if self._enabled:
            self._enabled = False
            self._disable_hooks()

    def results(self, result_type: ASResultType) -> List[Tensor]:
        if result_type == ASResultType.inputs_sparsity:
            res = self._inputs_sparsity
        elif result_type == ASResultType.inputs_sample:
            res = self._inputs_sample
        elif result_type == ASResultType.outputs_sparsity:
            res = self._outputs_sparsity
        elif result_type == ASResultType.outputs_sample:
            res = self._outputs_sample
        else:
            raise ValueError('result_type of {} is not supported'.format(result_type))

        if not res:
            res = torch.tensor([])

        res = [r if r.shape else r.unsqueeze(0) for r in res]

        return res

    def results_mean(self, result_type: ASResultType) -> Tensor:
        results = self.results(result_type)

        return torch.mean(torch.cat(results), dim=0)

    def results_std(self, result_type: ASResultType) -> Tensor:
        results = self.results(result_type)

        return torch.std(torch.cat(results), dim=0)
    
    def results_max(self, result_type: ASResultType) -> Tensor:
        results = self.results(result_type)
        
        return torch.max(torch.cat(results))
    
    def results_min(self, result_type: ASResultType) -> Tensor:
        results = self.results(result_type)
        
        return torch.min(torch.cat(results))

    def _enable_hooks(self):
        def _forward_pre_hook(_mod: Module, _inp: Union[Tensor, Tuple[Tensor]]):
            if not isinstance(_inp, Tensor):
                _inp = _inp[0]

            if self.track_inputs_sparsity:
                result = tensor_sparsity(_inp, dim=self.division)
                sparsities = result.detach_().cpu()
                self._inputs_sparsity.append(sparsities)

            if self.inputs_sample_size > 0:
                result = tensor_sparsity(_inp, dim=self.division)
                samples = result.detach_().cpu()
                self._inputs_sample.append(samples)

        def _forward_hook(_mod: Module, _inp: Union[Tensor, Tuple[Tensor]], _out: Union[Tensor, Tuple[Tensor]]):
            if not isinstance(_out, Tensor):
                _out = _out[0]

            if self.track_outputs_sparsity:
                result = tensor_sparsity(_out, dim=self.division)
                sparsities = result.detach_().cpu()
                self._outputs_sparsity.append(sparsities)

            if self.outputs_sample_size > 0:
                result = tensor_sample(_out, self.outputs_sample_size, dim=self.division)
                samples = result.detach_().cpu()
                self._outputs_sample.append(samples)

        self._pre_hook_handle = self.module.register_forward_pre_hook(_forward_pre_hook)
        self._hook_handle = self.module.register_forward_hook(_forward_hook)

    def _disable_hooks(self):
        if self._pre_hook_handle is not None:
            self._pre_hook_handle.remove()
            self._pre_hook_handle = None

        if self._hook_handle is not None:
            self._hook_handle.remove()
            self._hook_handle = None
