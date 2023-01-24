"""Classes for defining physical noise sources and measurement capabilities in a test environment."""

from math import floor, log10
import numpy as np
import pandas as pd
# For some reason using pd.Series doesn't play well with type annotations
from pandas import Series

from gerabaldi.models.randomvars import Deterministic, RandomVar
from gerabaldi.exceptions import InvalidTypeError

__all__ = ['MeasInstrument', 'EnvVrtnMdl', 'PhysTestEnv']


class MeasInstrument:
    """
    Defines the capabilities for measuring a given parameter. Defaults to an ideal device/instrument.

    Attributes
    ----------
    name : str, optional
        The name of the measurement instrument, usually the param it measures (default is 'generic')

    Methods
    -------
    measure(true_vals)
        Takes in exact parameter values and simulates the process of measuring them, returning the 'measured' values.
    """

    def __init__(self, name: str = 'generic', precision: int = None, error: RandomVar = None,
                 meas_lims: tuple = None):
        """
        Parameters
        ----------
        name : str, optional
            The name of the measurement instrument, usually the param it measures (default is 'generic')
        precision : int, optional
            The number of significant figures that the measurement device can provide (default is None)
        error : RandomVar, optional
            A statistical distribution representing noise and inherent error effects (default is None)
        meas_lims : tuple of int or float, optional
            The maximum and minimum values that the measurement device can report (default is None)
        """
        self.name = name
        self._precision = precision
        self._error = error
        self._range = meas_lims

    def measure(self, true_vals: Series | int | float | np.ndarray):
        """
        Returns a simulated measured value of a 'true' parameter value.

        Parameters
        ----------
        true_vals : int, float, ndarray, Series
            The set of underlying values to measure.

        Returns
        -------
        meas_vals : int, float, ndarray, Series
            The measured values formatted as the same type passed in.
        """

        # Identify the passed type, the return type will be formatted to match
        rtrn_type = type(true_vals)
        meas_vals = true_vals
        # Convert the input to a pandas Series if a different type
        if rtrn_type != pd.Series:
            meas_vals = pd.Series(meas_vals)

        if self._error:
            # Add offsets randomly sampled from the error statistical distribution
            meas_vals = meas_vals.add(self._error.sample(len(meas_vals)))
        if self._precision:
            # This line allows for rounding to significant figures instead of to a decimal place
            # The calculation for determining the sig figs -> decimal places is pretty standard and widely explained
            # Ternary operator is used to handle special cases that cause the rounding to fail otherwise
            meas_vals = meas_vals.apply(
                lambda x: x if x == 0 or np.isinf(x) else round(x, self._precision - int(floor(log10(abs(x)))) - 1))
        if self._range:
            # If the measured value exceeds the measurement range then force it to the limit
            meas_vals = meas_vals.clip(lower=self._range[0], upper=self._range[1])

        # Convert the measured values back into the passed data type
        if rtrn_type == np.ndarray:
            meas_vals = np.array(meas_vals)
        elif rtrn_type == int or rtrn_type == float:
            meas_vals = meas_vals[0]
        return meas_vals


class EnvVrtnMdl:
    """
    Specifies the full stochastic model used for generating variations in environmental conditions. This model is quite
    similar to the LatentVar class, with a few key differences related to batches vs. lots and unspecified base vals.
    """
    def __init__(self, dev_vrtn_mdl: RandomVar = None, chp_vrtn_mdl: RandomVar = None, batch_vrtn_mdl: RandomVar = None,
                 vrtn_type: str = 'offset', mdl_name: str = None):
        self.name = mdl_name
        self.vrtn_type = vrtn_type
        # Note that there are no lot variations here as the test environment is completely ignorant of what lot a device
        # comes from. The analog is variations between test batches, however the simulator treats these as
        # separate tests instead, and so the test would simply be run twice. The env_vrtn_mdl effectively provides a
        # batch variation model, with the number of batches enforced to be one per test.
        unitary = 0 if self.vrtn_type == 'offset' else 1
        self.batch_vrtn_mdl = batch_vrtn_mdl if batch_vrtn_mdl else Deterministic(unitary)
        self.dev_vrtn_mdl = dev_vrtn_mdl if dev_vrtn_mdl else Deterministic(unitary)
        self.chp_vrtn_mdl = chp_vrtn_mdl if chp_vrtn_mdl else Deterministic(unitary)
        # To understand why offset is default, consider temperature variation. If it was scaling, a 2% difference is
        # much greater at high temperatures, leading to variability coupled to the actual value. Although this can
        # work for latent variable values due to their fixed base value, this is unlikely to be
        # the intended behaviour for the vast majority of environmental conditions.
        if vrtn_type not in ['scaling', 'offset']:
            raise InvalidTypeError(f"Latent {self.name} variation type can only be one of 'scaling', 'offset'.")
        self.vrtn_op = vrtn_type

    def gen_env_vrtns(self, base_val: int | float, sensor_count: int = 0,
                      num_devs: int = 1, num_chps: int = 1, num_lots: int = 1) -> np.ndarray | tuple[np.ndarray, float]:
        """Generate stochastic variations for the specified number of individual samples, devices, and lots."""
        op = np.multiply if self.vrtn_op == 'scaling' else np.add
        # The device variations are held in a 3D array, allowing for easy indexing to the unique value for each sample
        vals = np.full((num_lots, num_chps, num_devs), base_val)
        # The generated arrays have to be carefully reshaped for the numpy array operators to broadcast them correctly
        vals = op(vals, self.dev_vrtn_mdl.
                  sample(num_lots * num_chps * num_devs).reshape((num_lots, num_chps, num_devs)))
        vals = op(vals, self.chp_vrtn_mdl.
                  sample(num_lots * num_chps).reshape((num_lots, num_chps, 1)))
        # Only sample once for the batch stochastic variation model
        batch_vrtn = self.batch_vrtn_mdl.sample()
        if sensor_count:
            return op(vals, batch_vrtn), op(op(op(np.full(sensor_count, base_val),
                                                  self.dev_vrtn_mdl.sample(sensor_count)),
                                            self.chp_vrtn_mdl.sample(sensor_count)), batch_vrtn)
        else:
            return op(vals, self.batch_vrtn_mdl.sample(1))


class PhysTestEnv:
    """
    Basic test environment in terms of measurement precisions and noise sources.

    Attributes
    ----------
    name : str
        The name of the test environment
    <prm_name>_var : RandomVar
        Stochastic distributions representing the variability for the environmental parameter in the attribute name
    <prm_name>_instm : MeasInstrument
        Measurement devices/instruments that will be used to measure the named parameter in the attribute name

    Methods
    -------
    get_meas_instm(prm)
        Retrieves the measurement instrument for a requested parameter
    get_vrtn_mdl(prm)
        Retrieves the variation/variability model for a requested parameter
    gen_env_cond_vals(base_vals)
        Varies a set of parameter values based on the variation models for those parameters
    """

    def __init__(self, env_vrtns: dict | list = None, meas_instms: dict | list = None, env_name: str = 'unspecified'):
        """
        Parameters
        ----------
        env_name : str, optional
            The name of the test environment, defaults to 'unspecified'
        env_vrtns : dict or list of EnvVrtnModel, optional
            Stochastic models representing the variability of the parameter names in the dict keys or dist names
        meas_instms : dict or list of MeasInstrument, optional
            Measurement devices used to measure values for the parameters named in the dict keys or dist names
        """
        self.name = env_name
        # Non-mutable default argument setup
        if env_vrtns is None:
            env_vrtns = []
        elif type(env_vrtns) == dict:
            # If passed as a dictionary, set the object names and transform to list
            for prm in env_vrtns:
                env_vrtns[prm].name = prm
            env_vrtns = [env_vrtns[prm] for prm in env_vrtns]
        if meas_instms is None:
            meas_instms = []
        elif type(meas_instms) == dict:
            for prm in meas_instms:
                meas_instms[prm].name = prm
            meas_instms = [meas_instms[prm] for prm in meas_instms]

        # Create attributes for each parameter
        for prm in env_vrtns:
            setattr(self, prm.name + '_var', prm)
        for prm in meas_instms:
            setattr(self, prm.name + '_instm', prm)

    def get_meas_instm(self, prm: str):
        """
        Get the associated measurement instrument for the param, if none exists then return an ideal one.

        Parameters
        ----------
        prm : str
            The parameter to be measured

        Returns
        -------
        MeasDevice
            A device to measure the parameter, either one previously defined for it or an ideal one if none exists
        """
        return getattr(self, prm + '_instm', MeasInstrument(prm))

    def get_vrtn_mdl(self, prm: str) -> EnvVrtnMdl:
        """
        Get the associated variability model for the passed parameter.

        Parameters
        ----------
        prm : str
            The parameter to find the corresponding variation model for

        Returns
        -------
        EnvVrtnMdl
            A stochastic model representing the parameter variability, if none associated returns an exact distribution
        """
        return getattr(self, prm + '_var', EnvVrtnMdl())

    def gen_env_cond_vals(self, base_vals: dict, num_vals: tuple | int = 1, sensor_counts: dict = None):
        """
        Generate 'true' values for test environment conditions by introducing variations to the intended values.

        Parameters
        ----------
        base_vals: dict of int or float
            The intended values for the parameters to be adjusted, the keys are the parameter names
        num_vals: int or tuple of int, optional
            Specifies the number and dimensional shape of values to generate per condition, defaults to 1
        sensor_counts: dict, optional
            If provided, additional conditional values will be generated for environmental sensors to measure

        Returns
        -------
        true_vals: dict of ndarray
            The adjusted/varied values identically formatted to the original intended values
        sensor_vals: dict of ndarray, only returned if 'sensor_counts' is provided
            Varied values representing the condition value at an environmental sensor head
        """
        num_vals = (1, 1, num_vals) if type(num_vals) == int else num_vals
        true_vals = {}
        sensor_vals = {}
        for cond in base_vals:
            if not sensor_counts or cond not in sensor_counts.keys():
                true_vals[cond] = self.get_vrtn_mdl(cond).\
                    gen_env_vrtns(base_vals[cond], num_devs=num_vals[2], num_chps=num_vals[1], num_lots=num_vals[0])
            else:
                true_vals[cond], sensor_vals[cond] = self.get_vrtn_mdl(cond)\
                    .gen_env_vrtns(base_vals[cond], sensor_counts[cond], num_vals[2], num_vals[1], num_vals[0])
        # Important design note: The reason the conditional values for the sensor readings are generated at the same
        # time as the ones for the device parameters is because the batch variations need to match between the sensors
        # and parameters. The individual and device variations are unique for the sensors, but the batch is shared.
        if not sensor_counts:
            return true_vals, {}
        else:
            return true_vals, sensor_vals
