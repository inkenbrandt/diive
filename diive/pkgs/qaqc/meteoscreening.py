from pathlib import Path

import numpy as np
import pandas as pd
from pandas import Series, DataFrame

from diive.common.dfun.frames import resample_df
from diive.common.io.dirs import verify_dir
from diive.common.io.filereader import ConfigFileReader
from diive.common.io.filereader import MultiDataFileReader, search_files
from diive.common.plotting.plotfuncs import quickplot_df
from diive.pkgs.corrections.offsetcorrection import remove_radiation_zero_offset, remove_relativehumidity_offset
from diive.pkgs.corrections.setto_threshold import setto_threshold
from diive.pkgs.createflag.outsiderange import range_check


class ScreenVar:
    """Quality screening of one single meteo data time series

    Typical time series: radiation, air temperature, soil water content.

    """

    def __init__(
            self,
            series: Series,
            measurement: str,
            units: str,
            site: str,
            site_lat: float = None,
            site_lon: float = None,
            saveplot: str or Path = None
    ):
        self.series = series
        self.measurement = measurement
        self.units = units
        self.site = site
        self.site_lat = site_lat
        self.site_lon = site_lon
        self.saveplot = saveplot

        # Processing pipes
        path = Path(__file__).parent.resolve()  # Search in this file's folder
        self.pipes = ConfigFileReader(configfilepath=path / 'pipes_meteo.yaml').read()
        self.pipe_config = self._pipe_assign()
        self.series_qc = self._call_pipe_steps()

    def get(self) -> Series:
        """Return corrected series"""
        return self.series_qc

    def _call_pipe_steps(self) -> Series:
        pipe_steps = self.pipe_config['pipe']
        series_qc = self.series.copy()
        series_qc.name = f"{series_qc.name}_qc"

        for step in pipe_steps:

            if step == 'remove_radiation_offset':  # todo include setto threshold separately
                series_qc = self._remove_radiation_offset(series=series_qc)

            elif step == 'remove_relativehumidity_offset':
                series_qc = self._remove_relativehumidity_offset(series=series_qc)

            elif step == 'setto_max_threshold':
                series_qc, _flag = self._setto_max_threshold(series=series_qc)

            elif step == 'setto_min_threshold':
                series_qc, _flag = self._setto_min_threshold(series=series_qc)

            elif step == 'range_check':
                series_qc, _flag = self._range_check(series=series_qc)

            else:
                raise Exception(f"No function defined for {step}.")

        return series_qc

    def _remove_relativehumidity_offset(self, series: Series) -> Series:
        return remove_relativehumidity_offset(series=series,
                                              show=True, saveplot=self.saveplot)

    def _setto_max_threshold(self, series: Series) -> tuple[Series, Series]:
        series_qc, flag = setto_threshold(series=series, threshold=self.pipe_config['range'][1],
                                          type='max', show=True, saveplot=self.saveplot)
        return series_qc, flag

    def _setto_min_threshold(self, series: Series) -> tuple[Series, Series]:
        series_qc, flag = setto_threshold(series=series, threshold=self.pipe_config['range'][0],
                                          type='min', show=True, saveplot=self.saveplot)
        return series_qc, flag

    def _remove_radiation_offset(self, series: Series) -> Series:
        if (self.site_lat is None) | (self.site_lon is None):
            raise Exception("Radiation offset requires site latitude and longitude.")
        return remove_radiation_zero_offset(series=series,
                                            lat=self.site_lat,
                                            lon=self.site_lon,
                                            show=True,
                                            saveplot=self.saveplot)

    def _range_check(self, series: Series) -> tuple[Series, Series]:
        series_qc, flag = range_check(series=series,
                                      min=self.pipe_config['range'][0],
                                      max=self.pipe_config['range'][1],
                                      show=True,
                                      saveplot=self.saveplot)
        return series_qc, flag

    def _pipe_assign(self) -> dict:
        """Assign appropriate processing pipe to this variable"""
        if self.measurement not in self.pipes.keys():
            raise KeyError(f"{self.measurement} is not defined in meteo_pipes.yaml")

        pipe_config = var = None
        for var in self.pipes[self.measurement].keys():
            if var in self.series.name:
                if self.pipes[self.measurement][var]['units'] != self.units:
                    continue
                pipe_config = self.pipes[self.measurement][var]
                break

        print(f"Variable '{self.series.name}' has been assigned to processing pipe {pipe_config['pipe']}")

        return pipe_config


class MeteoScreening:
    """Quality screening of selected variables in a dataframe

    """

    def __init__(
            self,
            df: DataFrame,
            cols: dict,
            site: str,
            site_lat: float = None,
            site_lon: float = None,
            outdir: str or Path = None,
    ):
        self.df = df
        self.cols = cols
        self.site = site
        self.site_lat = site_lat
        self.site_lon = site_lon
        self.outdir = Path(outdir)

        self._qc_df_resampled_gf = None

    @property
    def qc_df(self) -> DataFrame:
        """Get dataframe of quality checked data"""
        if not isinstance(self._qc_df_resampled_gf, DataFrame):
            raise Exception('data is empty')
        return self._qc_df_resampled_gf

    def run(self):
        if self.outdir:
            verify_dir(self.outdir)
        subset_df = self._subset()
        qc_df = self._screening_loop(subset_df=subset_df)
        qc_df_resampled = self._resampling(qc_df=qc_df)
        self._qc_df_resampled_gf = self._fill_missing(qc_df_resampled=qc_df_resampled)
        if self.outdir:
            self._export_to_file()

    def _export_to_file(self):
        self._qc_df_resampled_gf.to_csv(self.outdir / 'out.csv')
        quickplot_df(self._qc_df_resampled_gf.replace(-9999, np.nan),
                     title="** COLUMNS AFTER QUALITY CONTROL **",
                     saveplot=self.outdir)

    def _fill_missing(self, qc_df_resampled: DataFrame) -> DataFrame:
        qc_df_resampled.fillna(-9999, inplace=True)
        return qc_df_resampled

    def _resampling(self, qc_df: DataFrame) -> DataFrame:
        qc_df_resampled, _ = resample_df(df=qc_df, freq_str='30T', agg='mean',
                                         mincounts_perc=.9, to_freq='T')
        return qc_df_resampled

    def _screening_loop(self, subset_df: DataFrame) -> DataFrame:
        qc_df = pd.DataFrame()
        for col in self.cols.keys():
            series = subset_df[col].copy()
            measurement = self.cols[col]['measurement']
            units = self.cols[col]['units']
            col_qc = ScreenVar(series=series, measurement=measurement, units=units,
                               site=self.site, site_lat=self.site_lat, site_lon=self.site_lon,
                               saveplot=self.outdir).get()
            qc_df[col_qc.name] = col_qc
        return qc_df

    def _subset(self) -> DataFrame:
        subset_cols = []
        for col in self.cols.keys():
            subset_cols.append(col)
        subset_df = self.df[subset_cols].copy()

        if self.outdir:
            quickplot_df(subset_df.replace(-9999, np.nan),
                         title="COLUMNS *BEFORE* QUALITY CONTROL",
                         saveplot=self.outdir)

        return subset_df


if __name__ == '__main__':
    # todo compare radiation peaks for time shift
    # todo check outliers before AND after first qc check

    searchdir = r'C:\Users\holukas\Desktop\ch-aws\3_input_data_PA'
    pattern = '*.csv'
    filetype = 'CSV_1MIN'
    filepaths = search_files(searchdir=searchdir, pattern=pattern)
    mdfr = MultiDataFileReader(filepaths=filepaths, filetype=filetype)
    data = mdfr.data

    # df = data['df'][subset_cols].copy()
    # meta = data['meta'][subset_cols].copy()

    cols = {
        'PA_T1_1.3_1_1': {'measurement': 'PA', 'units': 'hPa'},
    }

    mscr = MeteoScreening(df=data['df'].copy(),
                          cols=cols,
                          site='ch-aws',
                          outdir=r'C:\Users\holukas\Desktop\ch-aws\4_screening_PA')
    mscr.run()
    qc_df = mscr.qc_df

    # Source data
    # searchdir = r'M:\Downloads\_temp'
    # pattern = '*.dat'
    # filetype = 'TOA5_DAT_1MIN'
    # filepaths = search_files(searchdir=searchdir, pattern=pattern)
    # mdfr = MultiDataFileReader(filepaths=filepaths, filetype=filetype)
    # data = mdfr.data
    #
    # # df = data['df'][subset_cols].copy()
    # # meta = data['meta'][subset_cols].copy()
    #
    # cols = {
    #     'SW_IN_M1B2_1x40_1_Avg': {'measurement': 'SW', 'units': 'W m-2'},
    #     'PPFD_IN_M1B2_1x40_1_Avg': {'measurement': 'PPFD', 'units': 'umol m-2 s-1'},
    #     'LW_IN_M1B2_1x40_1_Avg': {'measurement': 'LW', 'units': 'W m-2'},
    #     'RH_M1B1_1x50_1_Avg': {'measurement': 'RH', 'units': '%'},
    #     'TA_M1B1_1x50_1_Avg': {'measurement': 'TA', 'units': 'degC'},
    # }
    #
    # mscr = MeteoScreening(df=data['df'].copy(),
    #                       cols=cols,
    #                       site='ch-aws',
    #                       site_lat=46.583056,
    #                       site_lon=9.790639,
    #                       outdir=r'C:\Users\holukas\Desktop\ch-aws')
    # mscr.run()
    # qc_df = mscr.qc_df
