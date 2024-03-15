from typing import Literal

import pandas as pd
from pandas import DataFrame

from diive.core.funcs.funcs import validate_id_string
from diive.pkgs.qaqc.eddyproflags import (flags_vm97_eddypro_fulloutputfile_tests, \
                                          flags_vm97_eddypro_fluxnetfile_tests, flag_gas_completeness_eddypro_test, \
                                          flag_spectral_correction_factor_eddypro_test, flag_ssitc_eddypro_test,
                                          flag_angle_of_attack_eddypro_test,
                                          flag_steadiness_horizontal_wind_eddypro_test,
                                          flag_signal_strength_eddypro_test)
from diive.pkgs.qaqc.flags import MissingValues


class FluxQualityFlagsEddyPro:

    def __init__(self,
                 dfin: DataFrame,
                 fluxcol: str,
                 basevar: str,
                 filetype: Literal['EDDYPRO-FLUXNET-30MIN', 'EDDYPRO-FULL-OUTPUT-30MIN'],
                 idstr: str = None,
                 units: dict = None):
        """
        Create QCF (quality-control flag) for selected flags, calculated
        from EddyPro's _fluxnet_ or _full_output_ results files.

        Args:
            dfin: Dataframe containing EddyPro flux calculation results.
            fluxcol: Name of the flux variable in *dfin*.
            idstr: Suffix added to output variable names.
            filetype: Filetype of the input file.
            basevar: Name of the variable that was used to calculate the flux, e.g. 'CO2_CONC' for CO2 flux.
            units: Dictionary of columns names and their units, only needed
                when *filetype='EDDYPRO-FULL-OUTPUT-30MIN'*.
        """
        self.fluxcol = fluxcol
        self.dfin = dfin.copy()

        if not units and filetype == 'EDDYPRO-FULL-OUTPUT-30MIN':
            raise Exception("ERROR: No units found. Units are needed when working "
                            "with filetype EDDYPRO-FULL-OUTPUT-30MIN.")

        self.units = units
        self.idstr = validate_id_string(idstr=idstr)
        self.basevar = basevar
        self.filetype = filetype

        # Collect flags together with flux and potential radiation in separate dataframe
        self._results = self.dfin[[fluxcol]].copy()

    @property
    def results(self) -> DataFrame:
        """Return dataframe containing flags"""
        if not isinstance(self._results, DataFrame):
            raise Exception('Results for flux flags are empty')
        return self._results

    def angle_of_attack_test(self):
        flag = flag_angle_of_attack_eddypro_test(df=self.dfin, flux=self.fluxcol,
                                                 filetype=self.filetype, idstr=self.idstr)
        self._results[flag.name] = flag

    def steadiness_of_horizontal_wind(self):
        flag = flag_steadiness_horizontal_wind_eddypro_test(df=self.dfin, flux=self.fluxcol,
                                                            filetype=self.filetype, idstr=self.idstr)
        self._results[flag.name] = flag

    def raw_data_screening_vm97_tests(
            self,
            spikes: bool = True,
            amplitude: bool = False,
            dropout: bool = True,
            abslim: bool = False,
            skewkurt_hf: bool = False,
            skewkurt_sf: bool = False,
            discont_hf: bool = False,
            discont_sf: bool = False,
    ):
        kwargs = dict(
            df=self.dfin,
            units=self.units,
            flux=self.fluxcol,
            gas=self.basevar,
            idstr=self.idstr,
            spikes=spikes,
            amplitude=amplitude,
            dropout=dropout,
            abslim=abslim,
            skewkurt_hf=skewkurt_hf,
            skewkurt_sf=skewkurt_sf,
            discont_hf=discont_hf,
            discont_sf=discont_sf,
        )
        if self.filetype == 'EDDYPRO-FLUXNET-30MIN':
            flags = flags_vm97_eddypro_fluxnetfile_tests(**kwargs)
        elif self.filetype == 'EDDYPRO-FULL-OUTPUT-30MIN':
            flags = flags_vm97_eddypro_fulloutputfile_tests(**kwargs)
        else:
            raise Exception(f"Filetype {self.filetype.__name__} unkown.")
        self._results = pd.concat([self.results, flags], axis=1)

    def signal_strength_test(self,
                             signal_strength_col: str,
                             method: str,
                             threshold: int):
        flag = flag_signal_strength_eddypro_test(
            df=self.dfin, var_col=self.fluxcol, idstr=self.idstr,
            signal_strength_col=signal_strength_col,
            method=method, threshold=threshold
        )
        self._results[flag.name] = flag

    def spectral_correction_factor_test(self,
                                        thres_good: int = 2,
                                        thres_ok: int = 4):
        flag = flag_spectral_correction_factor_eddypro_test(
            df=self.dfin, flux=self.fluxcol, gas=self.basevar, idstr=self.idstr,
            filetype=self.filetype, thres_good=thres_good, thres_ok=thres_ok)
        self._results[flag.name] = flag

    def missing_vals_test(self):
        flagtest = MissingValues(series=self.dfin[self.fluxcol].copy(), idstr=self.idstr)
        flagtest.calc(repeat=False)
        flag = flagtest.get_flag()
        self._results[flag.name] = flag

    def ssitc_test(self):
        flag = flag_ssitc_eddypro_test(df=self.dfin, flux=self.fluxcol, filetype=self.filetype, idstr=self.idstr)
        self._results[flag.name] = flag

    def gas_completeness_test(self, thres_good: float = 0.99, thres_ok: float = 0.97):
        flag = flag_gas_completeness_eddypro_test(df=self.dfin, flux=self.fluxcol, gas=self.basevar,
                                                  filetype=self.filetype, idstr=self.idstr,
                                                  thres_good=thres_good, thres_ok=thres_ok)
        self._results[flag.name] = flag


def example():
    pass


if __name__ == '__main__':
    example()
