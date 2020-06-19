"""
Sensitivity analysis implementations for kernel sparsity on Modules against loss funcs.
"""

from typing import List, Callable, Any, Union, Tuple

from torch import Tensor
from torch.nn import Module
from torch.utils.data import DataLoader

from neuralmagicML.recal import KSLossSensitivityAnalysis, default_check_sparsities
from neuralmagicML.pytorch.utils import (
    ModuleTester,
    ModuleRunResults,
    LossWrapper,
    DEFAULT_LOSS_KEY,
    ModuleRunFuncs,
    get_prunable_layers,
    PyTorchLogger,
    infinite_data_loader,
)
from neuralmagicML.pytorch.recal.mask_ks import ModuleParamKSMask
from neuralmagicML.pytorch.recal.sparsity_mask import UnstructuredSparsityMaskCreator


__all__ = [
    "approx_ks_loss_sensitivity",
    "one_shot_ks_loss_sensitivity",
]


def approx_ks_loss_sensitivity(
    module: Module,
    sparsity_levels: Union[List[float], Tuple[float, ...]] = default_check_sparsities(
        True
    ),
):
    """
    Approximated kernel sparsity (pruning) loss analysis for a given model.
    Returns the results for each prunable param (conv, linear) in the model.

    :param module: the model to calculate the sparse sensitivity analysis for
    :param sparsity_levels: the sparsity levels to calculate the loss for for each param
    :return: the analysis results for the model
    """
    prunable = get_prunable_layers(module)
    analysis = KSLossSensitivityAnalysis()

    for index, (name, layer) in enumerate(prunable):
        weight = getattr(layer, "weight")
        values, _ = weight.view(-1).abs().sort()
        sparse_measurements = []
        prev_index = None

        for sparsity in sparsity_levels:
            val_index = round(sparsity * len(values))

            if val_index >= len(values):
                val_index = len(values) - 1

            if sparsity <= 0.0:
                sparse_measurements.append((sparsity, [0.0]))
            else:
                avg = values[prev_index:val_index].mean().item()
                sparse_measurements.append((sparsity, [avg]))

            prev_index = val_index + 1

        analysis.add_result("{}.weight".format(name), index, sparse_measurements)

    return analysis


def _sensitivity_callback(
    prunable_layers: List[Tuple[str, Module]],
    sparsity_levels: List[int],
    steps_per_measurement: int,
    analysis: KSLossSensitivityAnalysis,
    loss_key: str,
) -> Tuple[Callable, Callable]:
    measurement_steps = 0
    layer_index = -1
    sparsity_index = -1
    sparsity_results = None
    current_mask = None
    current_meas = None

    def complete_measurement():
        """
        Uses complete_measurement to handle when all of the required steps have been
        taken for a given layer and sparsity level.
        This handles saving the data for that measurement as well as incrementing to
        the next sparsity level. If all sparsity levels are complete,
        increments to the next layer and starts from the initial sparsity level
        while appending the final layer results to the analysis.

        Should only be invoked when all measurements have been taken,
        starting the entire process, or finishing the entire process.
        """

        nonlocal measurement_steps
        nonlocal layer_index
        nonlocal sparsity_index
        nonlocal sparsity_results
        nonlocal current_mask
        nonlocal current_meas

        if measurement_steps >= 0 and 0 <= layer_index < len(prunable_layers):
            ks_res = [
                res.item() for res in sparsity_results.result_list_tensor(loss_key)
            ]
            current_meas.append((sparsity_levels[sparsity_index], ks_res))

        measurement_steps = 0
        sparsity_index += 1
        sparsity_results = ModuleRunResults()

        if 0 <= sparsity_index < len(sparsity_levels) and 0 <= layer_index < len(
            prunable_layers
        ):
            current_mask.set_param_mask_from_sparsity(sparsity_levels[sparsity_index])
        else:
            if current_meas and layer_index < len(prunable_layers):
                analysis.add_result(
                    "{}.weight".format(prunable_layers[layer_index][0]),
                    sparsity_index,
                    current_meas,
                )

            sparsity_index = 0
            current_meas = []
            layer_index += 1

            if current_mask:
                current_mask.enabled = False
                current_mask.reset()
                del current_mask
                current_mask = None

            if layer_index < len(prunable_layers):
                current_mask = ModuleParamKSMask(
                    prunable_layers[layer_index][1],
                    store_init=True,
                    mask_creator=UnstructuredSparsityMaskCreator(),
                )
                current_mask.enabled = True

                if sparsity_levels[sparsity_index] > 0.0:
                    current_mask.set_param_mask_from_sparsity(
                        sparsity_levels[sparsity_index]
                    )

    complete_measurement()

    def batch_end(
        epoch: int, step: int, batch_size: int, data: Any, pred: Any, losses: Any,
    ):
        nonlocal measurement_steps
        measurement_steps += 1
        sparsity_results.append(losses, batch_size)

        if measurement_steps >= steps_per_measurement:
            complete_measurement()

    def completed():
        complete_measurement()

        return batch_end, completed

    return batch_end, completed


def one_shot_ks_loss_sensitivity(
    module: Module,
    data: DataLoader,
    loss: Union[LossWrapper, Callable[[Any, Any], Tensor]],
    device: str,
    steps_per_measurement: int,
    sparsity_levels: List[int] = default_check_sparsities(False),
    loss_key: str = DEFAULT_LOSS_KEY,
    tester_run_funcs: ModuleRunFuncs = None,
    tester_loggers: List[PyTorchLogger] = None,
    show_progress: bool = True,
) -> KSLossSensitivityAnalysis:
    """
    Run a one shot sensitivity analysis for kernel sparsity.
    It does not retrain, and instead puts the model to eval mode.
    Moves layer by layer to calculate the sensitivity analysis for each and
    resets the previously run layers.
    Note, by default it caches the data.
    This means it is not parallel for data loading and the first run can take longer.
    Subsequent sparsity checks for layers and levels will be much faster.

    :param module: the module to run the kernel sparsity sensitivity analysis over
        will extract all prunable layers out
    :param data: the data to run through the module for calculating the sensitivity
        analysis
    :param loss: the loss function to use for the sensitivity analysis
    :param device: the device to run the analysis on; ex: cpu, cuda
    :param steps_per_measurement: the number of samples or items to take for each
        measurement at each sparsity lev
    :param sparsity_levels: the sparsity levels to check for each layer to calculate
        sensitivity
    :param loss_key: the key for the loss function to track in the returned dict
    :param tester_run_funcs: override functions to use in the ModuleTester that runs
    :param tester_loggers: loggers to log data to while running the analysis
    :param show_progress: track progress of the runs if True
    :return: the sensitivity results for every layer that is prunable
    """
    analysis = KSLossSensitivityAnalysis()
    tester = ModuleTester(
        module,
        device,
        loss,
        loggers=tester_loggers,
        log_summary=False,
        log_steps=max(1, round(steps_per_measurement / 10)),
    )
    layers = get_prunable_layers(module)
    batch_end, completed = _sensitivity_callback(
        layers, sparsity_levels, steps_per_measurement, analysis, loss_key
    )
    batch_end_hook = tester.run_hooks.register_batch_end_hook(batch_end)
    if tester_run_funcs is not None:
        tester.run_funcs.copy(tester_run_funcs)

    data_loader = infinite_data_loader(
        data, early_stop_steps=steps_per_measurement, cache=True
    )
    tester.run(
        data_loader,
        desc="KS Analysis",
        show_progress=show_progress,
        track_results=False,
        max_steps=steps_per_measurement * len(sparsity_levels) * len(layers),
    )
    completed()
    batch_end_hook.remove()

    return analysis
