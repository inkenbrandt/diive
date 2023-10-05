# todo compare radiation peaks for time shift
# todo check outliers before AND after first qc check

"""
METEOSCREENING
==============

This module is part of the 'diive' library.

"""
from typing import Literal

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import pandas as pd
from pandas import DataFrame, Series
from pandas.tseries.frequencies import to_offset
from diive.pkgs.corrections.setto_value import setto_value
import diive.core.plotting.styles.LightTheme as theme
from diive.core.plotting.heatmap_datetime import HeatmapDateTime
from diive.core.plotting.plotfuncs import default_format, default_legend, nice_date_ticks
from diive.core.plotting.timeseries import TimeSeries
from diive.core.times.resampling import resample_series_to_30MIN
from diive.core.times.times import TimestampSanitizer
from diive.core.times.times import detect_freq_groups
from diive.pkgs.analyses.correlation import daily_correlation
from diive.pkgs.corrections.offsetcorrection import remove_radiation_zero_offset, remove_relativehumidity_offset
from diive.pkgs.corrections.setto_threshold import setto_threshold
from diive.pkgs.createvar.potentialradiation import potrad
from diive.pkgs.outlierdetection.stepwiseoutlierdetection import StepwiseOutlierDetection
from diive.pkgs.qaqc.qcf import FlagQCF


class StepwiseMeteoScreeningDb:
    """
    Stepwise MeteoScreening from database: Screen multiple vars from single measurement

    The class is optimized to work in Jupyter notebooks. Various outlier detection
    methods can be called on-demand. Outlier results are displayed and the user can
    accept the results and proceed, or repeat the step with adjusted method parameters.
    An unlimited amount of tests can be chained together. At the end of the screening,
    an overall flag is calculated from ALL single flags. The overall flag is then used
    to filter the time series.

    Implemented outlier tests:
    For a full list of outlier tests see: pkgs/outlierdetection/stepwiseoutlierdetection.py
    - `.flag_missingvals_test()`: Generate flag that indicates missing records in data
    - `.flag_outliers_abslim_test()`: Generate flag that indicates if values in data are outside the specified range
    - `.flag_outliers_increments_zcore_test()`: Identify outliers based on the z-score of increments
    - `.flag_outliers_localsd_test()`: Identify outliers based on the local standard deviation
    - `.flag_manualremoval_test()`: Remove data points for range, time or point-by-point
    - `.flag_outliers_stl_riqrz_test()`: Identify outliers based on seasonal-trend decomposition and z-score calculations
    - `.flag_outliers_thymeboost_test()`: Identify outliers based on [thymeboost](https://github.com/tblume1992/ThymeBoost)
    - `.flag_outliers_zscore_dtnt_test()`: Identify outliers based on the z-score, separately for daytime and nighttime
    - `.flag_outliers_zscore_test()`:  Identify outliers based on the z-score
    - `.flag_outliers_zscoreiqr_test()`: Identify outliers based on max z-scores in the interquartile range data
    - `.flag_outliers_lof_dtnt_test()`: Identify outliers based on local outlier factor, daytime nighttime separately
    - `.flag_outliers_lof_test()`: Identify outliers based on local outlier factor, across all data

    Implemented corrections:
    - `.correction_remove_radiation_zero_offset()`: Remove nighttime offset from all radiation data and set nighttime to zero
    - `.correction_remove_relativehumidity_offset()`: Remove relative humidity offset
    - `.correction_setto_max_threshold()`: Set values above a threshold value to threshold value
    - `.correction_setto_min_threshold()`: Set values below a threshold value to threshold value
    - `.correction_setto_value()`: Set records in time range(s) to constant value

    Implemented analyses:
    - `.analysis_potential_radiation_correlation()`: Analyzes time series daily correlation with potential radiation

    **Outlier tests**
    The stepwise meteoscreening allows to perform **step-by-step** quality tests on
    meteorological data. A preview plot after running a test is shown and the user can
    decide if results are satisfactory or if the same test with different parameters
    should be re-run. Once results are satisfactory, the respective test flag is added
    to the data with `.addflag()`. After running the desired tests, an overall flag
    `QCF` is calculated from all individual tests.

    **Corrections**
    In addition to the creation of quality flags, the stepwise screening allows to
    **correct data for common issues**. For example, short-wave radiation sensors
    often measure negative values during the night. These negative values are useful
    because they give info about the accuracy and precision of the sensor. In this
    case, values during the night should be zero. Instead of cutting off negative
    values, `diive` detects the nighttime offset for each day and then calculates
    a correction slope between individual days. This way, the daytime values are
    also corrected.

    **Resampling**
    After quality-screening and corrections, data are resampled to 30MIN time resolution.

    **Handling different time resolutions**
    One challenging aspect of the screening were the different time resolutions of the raw
    data. In some cases, the time resolution changed from e.g. 10MIN for older data to 1MIN
    for newer date. In cases of different time resolution, **the lower resolution is upsampled
    to the higher resolution**, the emerging gaps are *back-filled* with available data.
    Back-filling is used because the timestamp in the database always is TIMESTAMP_END, i.e.,
    it gives the *end* of the averaging interval. The advantage of upsampling is that all
    outlier detection routines can be applied to the whole dataset. Since data are resampled
    to 30MIN after screening and since the TIMESTAMP_END is respected, the upsampling itself
    has no impact on resulting aggregates.

    **Variables**
    The class allows the simultaneous quality-screening of multiple variables from one single
    measurement, e.g., multiple air temperature variables.

    **Database tags**
    Is optimized to work with the InfluxDB format of the ETH Grassland Sciences Group. The
    class can handle database tags and updates tags after data screening and resampling.

    **Modular structure**
    At the moment, the stepwise meteoscreening works for data downloaded from the `InfluxDB`
    database. The screening respects the database format (including tags) and prepares the
    screened, corrected and resampled data for direct database upload. Due to its modular
    approach, the stepwise screening can be easily adjusted to work with any type of data
    files. This adjustment will be done in one of the next updates.

    """

    def __init__(
            self,
            data_detailed: dict,
            # measurement: str,
            fields: list or str,
            site: str,
            site_lat: float,
            site_lon: float,
            timezone_of_timestamp: str
    ):
        self.site = site
        self._data_detailed = data_detailed.copy()
        # self.measurement = measurement
        self.fields = fields if isinstance(fields, list) else list(fields)
        self.site_lat = site_lat
        self.site_lon = site_lon
        self.timezone_of_timestamp = timezone_of_timestamp

        # Setup
        (
            self._series_hires_cleaned,
            self._series_hires_orig,
            self._tags,
            self._hires_flags,
            self._resampled_detailed,
            self._results_qcf,
            self._last_results
        ) = self._setup_fields()

        # Prepare step-wise outlier detection (sod) for each field
        # Each field gets its own sod object
        self.sod = {}
        for field in self.fields:
            self.sod[field] = StepwiseOutlierDetection(dataframe=self.data_detailed[field].copy(),
                                                       col=field,
                                                       site_lat=self.site_lat,
                                                       site_lon=self.site_lon,
                                                       timezone_of_timestamp=self.timezone_of_timestamp)

    @property
    def resampled_detailed(self) -> dict:
        """Return flag(s) as dict of Series"""
        if not isinstance(self._resampled_detailed, dict):
            raise Exception(f"No resampled data available.")
        return self._resampled_detailed

    @property
    def results_qcf(self) -> dict:
        """Return results from overall flag QCF calculation as dict of objects"""
        if not isinstance(self._results_qcf, dict):
            raise Exception(f"No QCF results available.")
        return self._results_qcf

    @property
    def data_detailed(self) -> dict:
        """Return high-resolution detailed data with tags as dict of DataFrames"""
        if not isinstance(self._data_detailed, dict):
            raise Exception(f"No high-resolution detailed data with tags available.")
        return self._data_detailed

    @property
    def last_results(self) -> dict:
        """Return high-resolution detailed data with tags as dict of objects"""
        if not isinstance(self._last_results, dict):
            raise Exception(f"No recent results available.")
        return self._last_results

    @property
    def tags(self) -> dict:
        """Return tags as dict of Series"""
        if not isinstance(self._tags, dict):
            raise Exception(f"No tags available.")
        return self._tags

    @property
    def series_hires_cleaned(self) -> dict:
        """Return cleaned time series of field(s) as dict of Series"""
        if not isinstance(self._series_hires_cleaned, dict):
            raise Exception(f"No hires quality-controlled data available.")
        return self._series_hires_cleaned

    @property
    def series_hires_orig(self) -> dict:
        """Return original time series of field(s) as dict of Series"""
        if not isinstance(self._series_hires_orig, dict):
            raise Exception(f"No hires original data available.")
        return self._series_hires_orig

    @property
    def hires_flags(self) -> dict:
        """Return flag(s) as dict of Series"""
        if not isinstance(self._hires_flags, dict):
            raise Exception(f"No hires flags available.")
        return self._hires_flags

    def showplot_qcf_heatmaps(self, **kwargs):
        for field in self.fields:
            self.results_qcf[field].showplot_qcf_heatmaps(**kwargs)

    def showplot_qcf_timeseries(self, **kwargs):
        for field in self.fields:
            self.results_qcf[field].showplot_qcf_timeseries(**kwargs)

    def showplot_resampled(self):
        """Show resampled data after high-resolution screening and corrections"""

        for field in self.fields:
            series_orig = self.series_hires_orig[field]
            series_resampled = self.resampled_detailed[field][field]

            fig = plt.figure(facecolor='white', figsize=(18, 9))
            gs = gridspec.GridSpec(3, 5)  # rows, cols
            gs.update(wspace=0.4, hspace=0.1, left=0.03, right=0.96, top=0.91, bottom=0.06)

            # Axes
            ax_orig = fig.add_subplot(gs[0, 0:3])
            ax_resampled = fig.add_subplot(gs[1, 0:3], sharex=ax_orig)
            ax_both = fig.add_subplot(gs[2, 0:3], sharex=ax_orig)
            ax_heatmap_hires_before = fig.add_subplot(gs[0:3, 3])
            ax_heatmap_resampled_after = fig.add_subplot(gs[0:3, 4], sharey=ax_heatmap_hires_before)

            # Time series
            ax_orig.plot_date(series_orig.index, series_orig, label=f"{series_orig.name}", color="#78909C",
                              alpha=.5, markersize=2, markeredgecolor='none')
            ax_resampled.plot_date(series_resampled.index, series_resampled, label=f"resampled",
                                   color="#FFA726", alpha=1, markersize=3, markeredgecolor='none')
            ax_both.plot_date(series_orig.index, series_orig, label=f"{series_orig.name}", color="#78909C",
                              alpha=.5, markersize=2, markeredgecolor='none')
            ax_both.plot_date(series_resampled.index, series_resampled, label=f"resampled",
                              color="#FFA726", alpha=1, markersize=3, markeredgecolor='none')

            # Heatmaps
            kwargs_heatmap = dict(cb_labelsize=10, axlabels_fontsize=10, ticks_labelsize=10,
                                  minyticks=3, maxyticks=99)
            HeatmapDateTime(ax=ax_heatmap_hires_before, series=series_orig, **kwargs_heatmap).plot()
            HeatmapDateTime(ax=ax_heatmap_resampled_after, series=series_resampled, **kwargs_heatmap).plot()

            # Format time series
            default_format(ax=ax_orig, ticks_labels_fontsize=10)
            default_format(ax=ax_resampled, ticks_labels_fontsize=10)
            default_format(ax=ax_both, ticks_labels_fontsize=10)
            nice_date_ticks(ax=ax_orig, minticks=3, maxticks=20, which='x', locator='auto')
            default_legend(ax=ax_orig, markerscale=3, textsize=10)
            default_legend(ax=ax_resampled, markerscale=3, textsize=10)
            default_legend(ax=ax_both, markerscale=3, textsize=10)
            plt.setp(ax_orig.get_xticklabels(), visible=False)
            plt.setp(ax_resampled.get_xticklabels(), visible=False)
            plt.setp(ax_heatmap_resampled_after.get_yticklabels(), visible=False)

            fig.suptitle(f"{self.series_hires_orig[field].name}: "
                         f"High-resolution before QC & corrections vs "
                         f"resampled after QC & corrections",
                         fontsize=theme.FIGHEADER_FONTSIZE)
            fig.show()

    def showplot_orig(self, interactive: bool = False):
        """Show original high-resolution data used as input"""
        for field in self.fields:
            p = TimeSeries(series=self.series_hires_cleaned[field])
            p.plot() if not interactive else p.plot_interactive()

    def showplot_cleaned(self, interactive: bool = False):
        """Show *current* cleaned high-resolution data"""
        for field in self.fields:
            p = TimeSeries(series=self.series_hires_cleaned[field])
            p.plot() if not interactive else p.plot_interactive()

    def report_qcf_evolution(self):
        for field in self.fields:
            self.results_qcf[field].report_qcf_evolution()

    def report_qcf_flags(self):
        for field in self.fields:
            self.results_qcf[field].report_qcf_flags()

    def report_qcf_series(self):
        for field in self.fields:
            self.results_qcf[field].report_qcf_series()

    def flag_missingvals_test(self):
        """Flag missing values"""
        for field in self.fields:
            self.sod[field].flag_missingvals_test()

    def flag_manualremoval_test(self, remove_dates: list, showplot: bool = False, verbose: bool = False):
        """Flag specified records for removal"""
        for field in self.fields:
            self.sod[field].flag_manualremoval_test(remove_dates=remove_dates,
                                                    showplot=showplot,
                                                    verbose=verbose)

    def flag_outliers_zscore_dtnt_test(self, threshold: float = 4, showplot: bool = False, verbose: bool = False):
        """z-score, calculated separately for daytime and nighttime"""
        for field in self.fields:
            self.sod[field].flag_outliers_zscore_dtnt_test(threshold=threshold,
                                                           showplot=showplot,
                                                           verbose=verbose)

    def flag_outliers_increments_zcore_test(self, threshold: int = 30, showplot: bool = False, verbose: bool = False):
        """Identify outliers based on the z-score of record increments"""
        for field in self.fields:
            self.sod[field].flag_outliers_increments_zcore_test(threshold=threshold,
                                                                showplot=showplot,
                                                                verbose=verbose)

    def flag_outliers_zscore_test(self, threshold: int = 4, showplot: bool = False, verbose: bool = False,
                                  plottitle: str = None):
        """Identify outliers based on the z-score of records"""
        for field in self.fields:
            self.sod[field].flag_outliers_zscore_test(threshold=threshold,
                                                      showplot=showplot,
                                                      verbose=verbose,
                                                      plottitle=plottitle)

    def flag_outliers_thymeboost_test(self, showplot: bool = False, verbose: bool = False):
        """Identify outliers based on thymeboost"""
        for field in self.fields:
            self.sod[field].flag_outliers_thymeboost_test(showplot=showplot,
                                                          verbose=verbose)

    def flag_outliers_localsd_test(self, n_sd: float, winsize: int = None, showplot: bool = False,
                                   verbose: bool = False):
        """Identify outliers based on standard deviation in a rolling window"""
        for field in self.fields:
            self.sod[field].flag_outliers_localsd_test(n_sd=n_sd,
                                                       winsize=winsize,
                                                       showplot=showplot,
                                                       verbose=verbose)

    def flag_outliers_abslim_test(self, minval: float, maxval: float, showplot: bool = False, verbose: bool = False):
        """Identify outliers based on absolute limits"""
        for field in self.fields:
            self.sod[field].flag_outliers_abslim_test(minval=minval,
                                                      maxval=maxval,
                                                      showplot=showplot,
                                                      verbose=verbose)

    def flag_outliers_abslim_dtnt_test(self, daytime_minmax: list[float, float],
                                       nighttime_minmax: list[float, float], showplot: bool = False):
        """Identify outliers based on absolute limits"""
        for field in self.fields:
            self.sod[field].flag_outliers_abslim_dtnt_test(daytime_minmax=daytime_minmax,
                                                           nighttime_minmax=nighttime_minmax,
                                                           showplot=showplot)

    def flag_outliers_stl_rz_test(self, zfactor: float = 4.5, decompose_downsampling_freq: str = '1H',
                                  repeat: bool = False, showplot: bool = False):
        """Identify outliers based on seasonal-trend decomposition and z-score calculations"""
        for field in self.fields:
            self.sod[field].flag_outliers_stl_rz_test(zfactor=zfactor,
                                                      decompose_downsampling_freq=decompose_downsampling_freq,
                                                      repeat=repeat,
                                                      showplot=showplot)

    def flag_outliers_lof_dtnt_test(self, n_neighbors: int = None, contamination: float = 'auto',
                                    showplot: bool = False, verbose: bool = False):
        """Local outlier factor, separately for daytime and nighttime data"""

        for field in self.fields:
            self.sod[field].flag_outliers_lof_dtnt_test(n_neighbors=n_neighbors,
                                                        contamination=contamination,
                                                        showplot=showplot,
                                                        verbose=verbose)

    def flag_outliers_lof_test(self, n_neighbors: int = None, contamination: float = 'auto',
                               showplot: bool = False, verbose: bool = False):
        """Local outlier factor, across all data"""

        for field in self.fields:
            self.sod[field].flag_outliers_lof_test(n_neighbors=n_neighbors,
                                                   contamination=contamination,
                                                   showplot=showplot,
                                                   verbose=verbose)

    def correction_remove_radiation_zero_offset(self):
        """Remove nighttime offset from all radiation data and set nighttime to zero"""
        for field in self.fields:
            self._series_hires_cleaned[field] = \
                remove_radiation_zero_offset(series=self._series_hires_cleaned[field],
                                             lat=self.site_lat, lon=self.site_lon,
                                             timezone_of_timestamp='UTC+01:00', showplot=True)

    def correction_setto_max_threshold(self, threshold: float):
        """Set values above threshold to threshold"""
        for field in self.fields:
            self._series_hires_cleaned[field] = \
                setto_threshold(series=self._series_hires_cleaned[field],
                                threshold=threshold, type='max', showplot=True)

    def correction_setto_min_threshold(self, threshold: float):
        """Set values below threshold to threshold"""
        for field in self.fields:
            self._series_hires_cleaned[field] = \
                setto_threshold(series=self._series_hires_cleaned[field],
                                threshold=threshold, type='min', showplot=True)

    def correction_setto_value(self, dates: list, value: float, verbose: int = 1):
        """Set records within time range to value"""
        for field in self.fields:
            self._series_hires_cleaned[field] = \
                setto_value(series=self._series_hires_cleaned[field],
                            dates=dates, value=value, verbose=verbose)

    def correction_remove_relativehumidity_offset(self):
        """Remove nighttime offset from all radiation data and set nighttime to zero"""
        for field in self.fields:
            self._series_hires_cleaned[field] = \
                remove_relativehumidity_offset(series=self._series_hires_cleaned[field], showplot=True)

    def analysis_potential_radiation_correlation(self,
                                                 utc_offset: int,
                                                 mincorr: float = 0.7,
                                                 showplot: bool = True) -> dict:
        """Compare time series to potential radiation

        Args:
            utc_offset: UTC offset of *radiation* timestamp
                For example, for European winter time *utc_offset=1*.
            mincorr: minimum absolute correlation, only relevant when *showplot=True*,
                must be between -1 and 1 (inclusive)
                Example: with *0.8* all correlations between -0.8 and +0.8 are considered low,
                and all correlations smaller than -0.8 and higher than +0.8 are considered high.
            showplot: if *True*, show plot of results

        Returns:
            dict of series with correlations for each field and for each day

        """

        daily_correlations = {}
        for field in self.fields:
            series = self.series_hires_cleaned[field]
            # Calculate potential radiation SW_IN_POT
            swinpot = potrad(timestamp_index=series.index,
                             lat=self.site_lat,
                             lon=self.site_lon,
                             utc_offset=utc_offset)

            # Calculate daily correlation between potential and measured observation
            daycorrs = daily_correlation(
                s1=series,
                s2=swinpot,
                mincorr=mincorr,
                showplot=showplot
            )
            daily_correlations[field] = daycorrs

        return daily_correlations

    def resample(self,
                 to_freqstr: Literal['30T'] = '30T',
                 agg: Literal['mean', 'sum'] = 'mean',
                 mincounts_perc: float = .25):

        for field in self.fields:

            # Resample to 30MIN
            series_resampled = resample_series_to_30MIN(series=self._series_hires_cleaned[field],
                                                        to_freqstr=to_freqstr,
                                                        agg=agg,
                                                        mincounts_perc=mincounts_perc)

            # Update tags with resampling info
            self._tags[field]['freq'] = '30T'
            self._tags[field]['data_version'] = 'meteoscreening'

            # Create df that includes the resampled series and its tags
            self._resampled_detailed[field] = pd.DataFrame()
            self._resampled_detailed[field][field] = series_resampled  # Store screened variable with original name
            self._resampled_detailed[field] = self._resampled_detailed[field].asfreq(series_resampled.index.freqstr)

            # Insert tags as columns
            for key, value in self._tags[field].items():
                self._resampled_detailed[field][key] = value

    def calc_qcf(self):
        """Calculate overall quality flag QCF and add QCF results to other flags"""
        for field in self.fields:
            series_orig = self._data_detailed[field][field].copy()

            qcf = FlagQCF(series=series_orig,
                          df=self.sod[field].hires_flags,
                          levelid='METSCR',
                          swinpot=None,
                          nighttime_threshold=50)
            qcf.calculate(daytime_accept_qcf_below=2, nighttimetime_accept_qcf_below=2)
            df_level32_qcf = qcf.get()

            self._hires_flags[field] = qcf.flags
            self._series_hires_cleaned[field] = qcf.filteredseries
            self._results_qcf[field] = qcf
            self._last_results[field] = qcf

            # series_orig = self._data_detailed[field][field].copy()
            # qcf = FlagQCF(df=self._hires_flags[field], series=series_orig)
            # qcf.calculate()
            # self._hires_flags[field] = qcf.flags
            # self._series_hires_cleaned[field] = qcf.filteredseries
            # self._results_qcf[field] = qcf
            # self._last_results[field] = qcf

    def addflag(self):
        """Add flag of most recent test to data and update filtered series
        that will be used to continue with the next test"""
        for field in self.fields:
            self.sod[field].addflag()
            self._series_hires_cleaned[field] = self.sod[field].series_hires_cleaned
            # flag = self._last_results[field].flag
            # self._series_hires_cleaned[field] = self._last_results[field].filteredseries
            # if not flag.name in self._hires_flags[field].columns:
            #     self._hires_flags[field][flag.name] = flag
            # else:
            #     pass  # todo check
            # # if flag.name in self._fulldf.columns:
            # #     self._fulldf.drop([flag.name], axis=1, inplace=True)
            # # self._fulldf[flag.name] = flag
            # print(f"++Added flag column {flag.name} to flag data")

    def _setup_fields(self):
        """Setup variable (field) data for meteoscreening"""
        # Loop over fields in measurement
        # Returned variables
        hires_flags = {}
        tags = {}
        series_hires_orig = {}
        series_hires_cleaned = {}
        resampled_detailed = {}
        results_qcf = {}
        last_results = {}  # Results of most recent QC tests (objects)

        for field in self.fields:
            data_detailed = self.data_detailed[field]  # Data for this field
            timestamp_name = data_detailed.index.name  # Get name of timestamp for later use
            self._check_units(data_detailed=data_detailed)
            self._check_fields(data_detailed=data_detailed)

            # Harmonize different time resolutions (upsampling to highest freq)
            groups = self._make_timeres_groups(data_detailed=data_detailed)
            group_counts = self._count_group_records(group_series=groups[field])
            targetfreq, used_freqs, rejected_freqs = self._validate_n_grouprecords(group_counts=group_counts)
            data_detailed = self._filter_data(data_detailed=data_detailed, used_freqs=used_freqs)
            data_detailed = self._harmonize_timeresolution(targetfreq=targetfreq, data_detailed=data_detailed,
                                                           timestamp_name=timestamp_name)
            data_detailed = self._sanitize_timestamp(targetfreq=targetfreq, data_detailed=data_detailed)

            # Store tags for this field in dict
            tags[field] = self._extract_tags(data_detailed=data_detailed, field=field)

            # Store data_detailed for this field in dict
            self._data_detailed[field] = data_detailed.copy()

            # Initialize quality flags for this field
            hires_flags[field] = self._init_flagsdf(data_detailed=data_detailed, field=field)

            # Store original timeseries for this field dict, will be cleaned
            series_hires_cleaned[field] = self._data_detailed[field][field].copy()  # Timeseries

            # Store original timeseries for this field dict, stays the same for later comparisons
            series_hires_orig[field] = self._data_detailed[field][field].copy()

            resampled_detailed[field] = {}
            results_qcf[field] = {}
            last_results[field] = {}

        return (series_hires_cleaned, series_hires_orig, tags, hires_flags,
                resampled_detailed, results_qcf, last_results)

    @staticmethod
    def _sanitize_timestamp(targetfreq, data_detailed):
        """
        Set frequency info and sanitize timestamp

        This also converts the timestamp to TIMESTAMP_MIDDLE.
        """
        offset = to_offset(pd.Timedelta(f'{targetfreq}S'))
        data_detailed = data_detailed.asfreq(offset.freqstr)
        data_detailed = TimestampSanitizer(data=data_detailed).get()
        return data_detailed

    @staticmethod
    def _harmonize_timeresolution(targetfreq, data_detailed, timestamp_name: str) -> DataFrame:
        """
        Create timestamp index of highest resolution and upsample
        lower resolution data

        Creates hires timestamp index between start and end date
        for data where the time resolution is not in target freq.
        For this purpose, the first date found in the data is not
        completely correct, because a TIMESTAMP_END of e.g.
        '2022-01-01 00:10' at 10MIN resolution is valid from
        '2022-01-01 00:01' until '2022-01-01 00:10' in a 1MIN
        timestamp index. The missing timestamp indexes are added
        here.
        """
        upsampleddf = pd.DataFrame()  # Collects upsampled data
        groups = data_detailed.groupby(data_detailed['FREQ_AUTO_SEC'])

        # Loop over different time resolutions
        for freq, groupdf in groups:

            # No upsampling for target freq, simply merge
            if freq == targetfreq:
                upsampleddf = pd.concat([upsampleddf, groupdf], axis=0)
                continue

            # Add missing timestamp indexes at start of data
            start = groupdf.index[0] - pd.Timedelta(seconds=freq)

            # Create hires timestamp index between start and end dates
            hires_ix = pd.date_range(start=start,
                                     end=groupdf.index[-1],
                                     freq=f'{targetfreq}S')

            # If target freq is e.g. 60S (1MIN) and current freq is 600S (10MIN)
            # then the 600S records are valid for ten 60S records, whereby
            # one original record is already available
            # limit = (600 / 60) - 1 = 9 records to fill
            limit = int((freq / targetfreq) - 1)

            # The timestamp is TIMESTAMP_END, therefore 'backfill'
            cur_upsampleddf = groupdf.reindex(hires_ix)
            cur_upsampleddf = cur_upsampleddf.fillna(method='backfill', limit=limit)

            # Delete first timestamp index, outside limit
            cur_upsampleddf = cur_upsampleddf.iloc[1:].copy()

            # Add to upsampled data
            # upsampleddf = pd.concat([upsampleddf, cur_upsampleddf], axis=0)
            # Better use .combine_first to avoid duplicates
            upsampleddf = upsampleddf.combine_first(cur_upsampleddf)

        # Sort timestamp index ascending
        upsampleddf = upsampleddf.sort_index(ascending=True)
        upsampleddf.index.name = timestamp_name

        # upsampleddf.index.duplicated().sum()

        # import matplotlib.pyplot as plt
        # upsampleddf['TA_NABEL_T1_35_1'].plot()
        # plt.show()
        return upsampleddf

    @staticmethod
    def _extract_tags(data_detailed, field) -> dict:
        """For each variable, extract tag columns from the respective DataFrame
         and store info in simplified dict"""
        tags_df = data_detailed.drop(columns=[field, 'FREQ_AUTO_SEC'])
        # tags_df.nunique()
        notags = tags_df.isnull().all(axis=1)
        tags_df = tags_df[~notags]  # Drop rows where all tags are missing; this is the case due to upsampling
        tags_dict = {}
        for tag in tags_df.columns:
            list_of_vals = list(tags_df[tag].unique())
            str_of_vals = ",".join([str(i) for i in list_of_vals])
            tags_dict[tag] = str_of_vals
        return tags_dict

    @staticmethod
    def _init_flagsdf(data_detailed, field) -> DataFrame:
        """Initialize dataframe that will contain all flags for each variable"""
        series = data_detailed[field]  # Timeseries of variable
        hires_flags = pd.DataFrame(index=series.index)
        return hires_flags

    @staticmethod
    def _check_units(data_detailed):
        """Check if units are the same for all records"""
        unique_units = list(set(data_detailed['units']))
        if len(unique_units) > 1:
            raise Exception(f"More than one type of units in column 'units', "
                            f"but only one allowed. All data records must be "
                            f"in same units.")

    @staticmethod
    def _check_fields(data_detailed):
        """Check if really only one field in data"""
        unique_fields = list(set(data_detailed['varname']))
        if len(unique_fields) > 1:
            raise Exception(f"More than one variable name in column 'varname', "
                            f"but only one allowed. All data records must be "
                            f"for same variable.")

    @staticmethod
    def _make_timeres_groups(data_detailed):
        """Group data by time resolution"""
        groups_ser = detect_freq_groups(index=data_detailed.index)
        data_detailed[groups_ser.name] = groups_ser
        groups = data_detailed.groupby(data_detailed['FREQ_AUTO_SEC'])
        return groups

    @staticmethod
    def _count_group_records(group_series):
        """Count records for each found time resolution"""
        group_counts = group_series.count().sort_values(ascending=False)
        return group_counts

    @staticmethod
    def _validate_n_grouprecords(group_counts) -> tuple[float, list, list]:
        """Detect which frequencies have enough records to be used"""
        n_vals = group_counts.sum()
        n_freqs = group_counts.index.unique()
        print(f"Found {len(n_freqs)} unique frequencies across {n_vals} records.")
        print("Found frequencies:")
        cumulative_counts = 0
        used_freqs = []
        rejected_freqs = []
        for freq in n_freqs:
            counts = group_counts[freq]
            cumulative_counts += counts
            counts_perc = (counts / n_vals) * 100
            print(f"    Found time resolution {freq} (seconds) with {counts} records "
                  f"({counts_perc:.2f}% of total records).", end=" ")
            if counts_perc > 0.01:
                used_freqs.append(freq)
                print("")
            else:
                rejected_freqs.append(rejected_freqs)
                print("  -->  Frequency will be ignored, too few records.")
        print(f"The following frequencies will be used: {used_freqs} (seconds)")
        targetfreq = min(used_freqs)
        if len(used_freqs) > 1:
            print(f"Note that there is more than one single time resolution and "
                  f"all data will be upsampled to match the highest found time "
                  f"resolution ({targetfreq}S).")
        return targetfreq, used_freqs, rejected_freqs

    def _filter_data(self, data_detailed, used_freqs):
        data_detailed = data_detailed.loc[data_detailed['FREQ_AUTO_SEC'].isin(used_freqs)]
        return data_detailed


def example():
    from pathlib import Path

    TESTDIR = Path(r"F:\Sync\luhk_work\TMP")

    # User settings, site
    SITE = 'ch-cha'
    SITE_LAT = 47.210222
    SITE_LON = 8.410444

    # User settings, variables to screen
    # FIELDS = ['TS_GF1_0.05_1']
    FIELDS = ['PREC_RAIN_TOT_GF1_0.5_1']
    MEASUREMENT = 'PREC'

    # User settings, time range to screen
    START = '2022-01-01 00:00:01'
    STOP = '2023-01-01 00:00:01'

    # Auto-settings, data settings
    DATA_VERSION = 'raw'
    TIMEZONE_OFFSET_TO_UTC_HOURS = 1  # Timezone, e.g. "1" is translated to timezone "UTC+01:00" (CET, winter time)
    RESAMPLING_FREQ = '30T'  # During MeteoScreening the screened high-res data will be resampled to this frequency; '30T' = 30-minute time resolution
    RESAMPLING_AGG = 'sum'  # The resampling of the high-res data will be done using this aggregation methos; e.g., 'mean'
    # DIRCONF = r'P:\Flux\RDS_calculations\_scripts\_configs\configs'  # Location of configuration files, needed e.g. for connection to database
    DIRCONF = r'F:\Sync\luhk_work\20 - CODING\22 - POET\configs'

    # Auto-settings, imports
    from datetime import datetime
    import importlib.metadata
    # %matplotlib inline
    from bokeh.plotting import output_notebook
    output_notebook()
    # from diive.pkgs.qaqc.meteoscreening import StepwiseMeteoScreeningDb
    dt_string = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"This page was last modified on: {dt_string}")
    version_diive = importlib.metadata.version("diive")
    print(f"diive version: v{version_diive}")

    # Auto-settings, automatic settings
    BUCKET_RAW = f'{SITE}_raw'  # The 'bucket' where data are stored in the database, e.g., 'ch-lae_raw' contains all raw data for CH-LAE
    BUCKET_PROCESSING = f'{SITE}_processing'  # The 'bucket' where data are stored in the database, e.g., 'ch-lae_processing' contains all processed data for CH-LAE
    print(f"Bucket containing raw data (source bucket): {BUCKET_RAW}")
    print(f"Bucket containing processed data (destination bucket): {BUCKET_PROCESSING}")

    # Download data from database with "dbc-influxdb"
    from dbc_influxdb import dbcInflux
    dbc = dbcInflux(dirconf=DIRCONF)  # Instantiate class

    # data_simple, data_detailed, assigned_measurements = \
    #     dbc.download(bucket=BUCKET_RAW,
    #                  measurements=[MEASUREMENT],
    #                  fields=FIELDS,
    #                  start=START,
    #                  stop=STOP,
    #                  timezone_offset_to_utc_hours=TIMEZONE_OFFSET_TO_UTC_HOURS,
    #                  data_version='raw')
    # import matplotlib.pyplot as plt
    # data_simple.plot()
    # plt.show()
    #
    # # Export data to pickle and parquet for fast testing
    # from diive.core.io.files import save_parquet, save_as_pickle
    # save_parquet(filename="meteodata_simple", data=data_simple, outpath=TESTDIR)
    # save_as_pickle(filename="meteodata_detailed", data=data_detailed, outpath=TESTDIR)
    # save_as_pickle(filename="meteodata_assigned_measurements", data=assigned_measurements, outpath=TESTDIR)

    # Import data from pickle for fast testing
    from diive.core.io.files import load_parquet, load_pickle
    data_simple = load_parquet(filepath=TESTDIR / "meteodata_simple.parquet")
    _f = str(TESTDIR / "meteodata_detailed.pickle")
    data_detailed = load_pickle(_f)
    _f = str(TESTDIR / "meteodata_assigned_measurements.pickle")
    assigned_measurements = load_pickle(_f)

    print(f"Data available for: {data_detailed.keys()}\n")
    vars_not_available = [v for v in FIELDS if v not in data_detailed.keys()]
    print(f"No data available for the following variables: {vars_not_available}")
    for rem in vars_not_available:
        print(rem)
        FIELDS.remove(rem)
        print(f"Removed variables {rem} from FIELDS because it is not available during this time period.")

    # # Restrict data for testing
    # from diive.core.dfun.frames import df_between_two_dates
    # for key in data_detailed.keys():
    #     data_detailed[key] = df_between_two_dates(df=data_detailed[key], start_date='2022-06-01', end_date='2022-06-30')

    # Start MeteoScreening session
    mscr = StepwiseMeteoScreeningDb(site=SITE,
                                    data_detailed=data_detailed,
                                    # measurement=MEASUREMENT,
                                    fields=FIELDS,
                                    site_lat=SITE_LAT,
                                    site_lon=SITE_LON,
                                    timezone_of_timestamp='UTC+01:00')

    # Plot data
    mscr.showplot_orig()
    # mscr.showplot_cleaned()

    # Missing values test
    mscr.flag_missingvals_test()
    mscr.addflag()

    # # Manual removal
    # REMOVE_DATES = [
    #     ['2005-12-30 13:00:00', '2005-12-30 16:00:00'],  # delete, bad data, CNR1 covered by snow/ice
    #     ['2006-07-21 16:00:00', '2006-07-21 16:00:00'],  # delete, bad data,
    # ]
    # mscr.flag_manualremoval_test(remove_dates=REMOVE_DATES,
    #                              showplot=True, verbose=True)
    # mscr.addflag()
    # mscr.showplot_cleaned()

    # # Outlier detection: Absolute limits
    # mscr.flag_outliers_abslim_test(minval=-50, maxval=1000, showplot=True)
    # mscr.addflag()
    # mscr.showplot_cleaned()

    # # Outlier detection: Absolute limits, separate for daytime and nighttime
    # mscr.flag_outliers_abslim_dtnt_test(daytime_minmax=[-50, 2000], nighttime_minmax=[-50, 2000], showplot=True)
    # mscr.addflag()
    # mscr.showplot_cleaned()

    # # Outlier detection: Absolute limits, separate for daytime and nighttime
    # mscr.flag_outliers_stl_rz_test(zfactor=4.5, decompose_downsampling_freq='6H', repeat=False, showplot=True)
    # mscr.addflag()

    # # Outlier detection: z-score over all data
    # mscr.flag_outliers_zscore_test(threshold=4, showplot=True, verbose=True)
    # mscr.addflag()

    # # Outlier detection: z-score over all data, separate for daytime and nighttime
    # mscr.flag_outliers_zscore_dtnt_test(threshold=5, showplot=True, verbose=True)
    # mscr.addflag()

    # # Outlier detection: Increments z-score
    # mscr.flag_outliers_increments_zcore_test(threshold=50, showplot=True)
    # mscr.addflag()

    # # Outlier detection: Thymeboost
    # mscr.flag_outliers_thymeboost_test(showplot=True)
    # mscr.addflag()

    # # Outlier detection: Local SD
    # mscr.flag_outliers_localsd_test(n_sd=5, winsize=None, showplot=True)
    # mscr.addflag()

    # # Outlier detection: Local outlier factor, across all data
    # # todo hires data problematic, and reduce njobs
    # mscr.flag_outliers_lof_test(n_neighbors=None, showplot=True, verbose=True)
    # mscr.addflag()

    # # Outlier detection: Local outlier factor, daytime nighttime
    # # todo hires data problematic
    # mscr.flag_outliers_lof_dtnt_test(n_neighbors=None, showplot=True, verbose=True)
    # mscr.addflag()

    # After all QC flags generated, calculate overall flag QCF
    mscr.calc_qcf()

    # QCF reports
    mscr.report_qcf_evolution()
    # mscr.report_qcf_flags()
    # mscr.report_qcf_series()
    # mscr.showplot_qcf_heatmaps()
    # mscr.showplot_qcf_timeseries()

    # Apply corrections
    # mscr.correction_remove_radiation_zero_offset()
    # mscr.correction_setto_max_threshold(threshold=400)
    # mscr.correction_setto_min_threshold(threshold=100)
    DATES = [
        ['2022-03-01 00:00:01', '2022-09-15 00:00:01'],
        ['2022-08-15 00:00:01', '2022-09-01 00:00:01']
    ]
    mscr.correction_setto_value(dates=DATES, value=0, verbose=1)
    # mscr.correction_remove_relativehumidity_offset()

    mscr.analysis_potential_radiation_correlation(utc_offset=1,
                                                  mincorr=0.7,
                                                  showplot=True)

    # End MeteoScreening session
    mscr.resample(to_freqstr='30T', agg=RESAMPLING_AGG, mincounts_perc=.25)
    mscr.showplot_resampled()

    for v in mscr.resampled_detailed.keys():
        m = assigned_measurements[v]
        dbc.upload_singlevar(to_bucket=BUCKET_PROCESSING,
                             to_measurement=m,
                             var_df=mscr.resampled_detailed[v],
                             timezone_of_timestamp='UTC+01:00')


if __name__ == '__main__':
    example()
