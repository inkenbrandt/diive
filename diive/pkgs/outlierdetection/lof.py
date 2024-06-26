"""
OUTLIER DETECTION: LOCAL OUTLIER FACTOR
=======================================

This module is part of the diive library:
https://github.com/holukas/diive

Local outlier factor:
    "The anomaly score of each sample is called the Local Outlier Factor. It measures
    the local deviation of the density of a given sample with respect to its neighbors.
    It is local in that the anomaly score depends on how isolated the object is with
    respect to the surrounding neighborhood. More precisely, locality is given by
    k-nearest neighbors, whose distance is used to estimate the local density. By
    comparing the local density of a sample to the local densities of its neighbors,
    one can identify samples that have a substantially lower density than their neighbors.
    These are considered outliers." - scikit-learn documentation, 6 Jan 2024

Kudos:
    - https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html
    - https://scikit-learn.org/stable/modules/outlier_detection.html
    - https://scikit-learn.org/stable/auto_examples/neighbors/plot_lof_outlier_detection.html#sphx-glr-auto-examples-neighbors-plot-lof-outlier-detection-py
    - https://www.datatechnotes.com/2020/04/anomaly-detection-with-local-outlier-factor-in-python.html

References:
    [1] https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.LocalOutlierFactor.html

"""

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from numpy import where
from pandas import Series, DatetimeIndex, DataFrame
from sklearn.neighbors import LocalOutlierFactor

import diive.core.plotting.styles.LightTheme as theme
from diive.core.base.flagbase import FlagBase
from diive.core.plotting.plotfuncs import default_format, default_legend
from diive.core.utils.prints import ConsoleOutputDecorator
from diive.pkgs.createvar.daynightflag import DaytimeNighttimeFlag


def lof(series: Series,
        n_neighbors: int = 20,
        contamination: float = 0.01,
        suffix: str = None,
        n_jobs: int = 1) -> DataFrame:
    """Unsupervised Outlier Detection using the Local Outlier Factor (LOF)."""

    # Prepare data
    if not suffix:
        suffix = ""
    series = series.copy().dropna()
    ix = series.index
    vals = series.to_numpy().reshape(-1, 1)

    # Run analysis
    lof = LocalOutlierFactor(
        n_neighbors=n_neighbors,  # default=20
        algorithm='auto',  # algorithm: {‘auto’, ‘ball_tree’, ‘kd_tree’, ‘brute’}, default=’auto’
        leaf_size=30,  # default=30
        metric='minkowski',
        p=2,
        metric_params=None,
        contamination=contamination,
        novelty=False,
        n_jobs=n_jobs
    )
    y_pred = lof.fit_predict(vals)

    # Outlier indexes
    lofs_index = where(y_pred == -1)
    outlier_vals = vals[lofs_index]
    outlier_vals = outlier_vals[:, 0]  # Convert to array
    outlier_ix = ix[lofs_index]

    vals = vals[:, 0]

    # Collect in dataframe
    series = pd.Series(index=ix, data=vals)
    series_outliers = pd.Series(index=outlier_ix, data=outlier_vals)
    frame = {f'SERIES_{suffix}': series,
             f'OUTLIER_{suffix}': series_outliers}
    df = pd.DataFrame(frame)
    df[f'NOT_OUTLIER_{suffix}'] = df[f'SERIES_{suffix}'].copy()
    df[f'NOT_OUTLIER_{suffix}'].loc[series_outliers.index] = np.nan

    return df


@ConsoleOutputDecorator()
class LocalOutlierFactorAllData(FlagBase):

    flagid = 'OUTLIER_LOF'

    def __init__(self,
                 series: Series,
                 idstr: str = None,
                 n_neighbors: int = 20,
                 contamination: float = 0.01,
                 showplot: bool = False,
                 verbose: bool = False,
                 n_jobs: int = 1):
        """Identify outliers based on the local outlier factor.

        Args:
            series: Time series in which outliers are identified.
            idstr: Identifier, added as suffix to output variable names.
            n_neighbors: Number of neighbors to use by default for kneighbors queries.
                If n_neighbors is larger than the number of samples provided, all samples
                will be used (description taken from scikit, ref [1])
            contamination:The amount of contamination of the data set, i.e. the proportion
                of outliers in the data set. When fitting this is used to define the threshold
                 on the scores of the samples.
                 - if ‘auto’, the threshold is determined as in the original paper,
                 - if a float, the contamination should be in the range (0, 0.5].
                (description taken from scikit, ref [1])
            n_jobs: The number of parallel jobs to run for neighbors search. None means 1
                unless in a joblib.parallel_backend context. -1 means using all processors.
                (description taken from scikit, ref [1])
            showplot: Show plot with results from the outlier detection.
            verbose: Print more text output.

        Returns:
            Results dataframe via the @repeater wrapper function, dataframe contains
            the filtered time series and flags from all iterations.

        """
        super().__init__(series=series, flagid=self.flagid, idstr=idstr)
        self.showplot = False
        self.verbose = False
        self.n_neighbors = n_neighbors
        self.contamination = contamination
        self.showplot = showplot
        self.verbose = verbose
        self.n_jobs = n_jobs

    def calc(self, repeat: bool = True):
        """Calculate overall flag, based on individual flags from multiple iterations.

        Args:
            repeat: If *True*, the outlier detection is repeated until all
                outliers are removed.

        """

        self._overall_flag, n_iterations = self.repeat(self.run_flagtests, repeat=repeat)
        if self.showplot:
            self.defaultplot(n_iterations=n_iterations)

    def _flagtests(self, iteration) -> tuple[DatetimeIndex, DatetimeIndex, int]:
        """Perform tests required for this flag"""

        flag = pd.Series(index=self.filteredseries.index, data=np.nan)

        s = self.filteredseries.copy()
        _df = lof(series=s, n_neighbors=self.n_neighbors,
                  contamination=self.contamination, suffix="", n_jobs=self.n_jobs)
        ok = _df['NOT_OUTLIER_'].dropna().index
        rejected = _df['OUTLIER_'].dropna().index

        # Create flag
        flag.loc[ok] = 0
        flag.loc[rejected] = 2

        # Collect data in dataframe
        df = pd.DataFrame(self.filteredseries)
        df = pd.concat([df, _df], axis=1)
        df['FLAG'] = flag

        df['CLEANED'] = df[self.filteredseries.name].copy()
        df['CLEANED'].loc[df['FLAG'] > 0] = np.nan

        n_outliers = (flag == 2).sum()

        ok = (flag == 0)
        ok = ok[ok].index
        rejected = (flag == 2)
        rejected = rejected[rejected].index

        if self.verbose:
            print(f"ITERATION#{iteration}: Total found outliers: {n_outliers} values (daytime+nighttime)")

        if self.showplot:
            self._plot(df=df)

        return ok, rejected, n_outliers

    def _plot(self, df: DataFrame):
        fig = plt.figure(facecolor='white', figsize=(12, 16))
        gs = gridspec.GridSpec(3, 1)  # rows, cols
        gs.update(wspace=0.3, hspace=0.1, left=0.05, right=0.95, top=0.95, bottom=0.05)
        ax1 = fig.add_subplot(gs[0, 0])
        ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
        ax3 = fig.add_subplot(gs[2, 0], sharex=ax1)

        ax1.plot_date(x=df.index, y=df[self.filteredseries.name], fmt='o', mec='none',
                      alpha=.5, color='black', label="series")

        ax2.plot_date(x=df.index, y=df['CLEANED'], fmt='o', mec='none',
                      alpha=.5, label="cleaned series")

        ax3.plot_date(x=df.index, y=df['NOT_OUTLIER_'], fmt='o', mec='none',
                      alpha=.5, label="OK")
        ax3.plot_date(x=df.index, y=df['OUTLIER_'], fmt='o', mec='none',
                      alpha=.5, color='red', label="outlier")

        default_format(ax=ax1)
        default_format(ax=ax2)
        default_format(ax=ax3)

        default_legend(ax=ax1)
        default_legend(ax=ax2)
        default_legend(ax=ax3)

        plt.setp(ax1.get_xticklabels(), visible=False)
        plt.setp(ax2.get_xticklabels(), visible=False)
        plt.setp(ax3.get_xticklabels(), visible=False)

        title = f"Outlier detection - local outlier factor"
        fig.suptitle(title, fontsize=theme.FIGHEADER_FONTSIZE)
        fig.show()


@ConsoleOutputDecorator()
class LocalOutlierFactorDaytimeNighttime(FlagBase):

    flagid = 'OUTLIER_LOFDTNT'

    def __init__(self,
                 series: Series,
                 lat: float,
                 lon: float,
                 utc_offset: int,
                 idstr: str = None,
                 n_neighbors: int = 20,
                 contamination: float = 0.01,
                 showplot: bool = False,
                 verbose: bool = False,
                 n_jobs: int = 1):
        """Identify outliers based on the local outlier factor, done separately for
        daytime and nighttime data.

        Args:
            series: Time series in which outliers are identified.
            lat: Latitude of location as float, e.g. 46.583056
            lon: Longitude of location as float, e.g. 9.790639
            utc_offset:
            idstr: Identifier, added as suffix to output variable names.
            n_neighbors: Number of neighbors to use by default for kneighbors queries.
                If n_neighbors is larger than the number of samples provided, all samples
                will be used (description taken from scikit, ref [1])
            contamination:The amount of contamination of the data set, i.e. the proportion
                of outliers in the data set. When fitting this is used to define the threshold
                 on the scores of the samples.
                 - if ‘auto’, the threshold is determined as in the original paper,
                 - if a float, the contamination should be in the range (0, 0.5].
                (description taken from scikit, ref [1])
            showplot: Show plot with results from the outlier detection.
            verbose: Print more text output.
            n_jobs: The number of parallel jobs to run for neighbors search. None means 1
                unless in a joblib.parallel_backend context. -1 means using all processors.
                (description taken from scikit, ref [1])

        Returns:
            Results dataframe via the @repeater wrapper function, dataframe contains
            the filtered time series and flags from all iterations.

        """
        super().__init__(series=series, flagid=self.flagid, idstr=idstr)
        self.showplot = False
        self.verbose = False
        self.n_neighbors = n_neighbors
        self.contamination = contamination
        self.showplot = showplot
        self.verbose = verbose
        self.n_jobs = n_jobs

        # Detect daytime and nighttime
        dnf = DaytimeNighttimeFlag(
            timestamp_index=self.series.index,
            nighttime_threshold=50,
            lat=lat,
            lon=lon,
            utc_offset=utc_offset)
        nighttimeflag = dnf.get_nighttime_flag()
        daytimeflag = dnf.get_daytime_flag()
        self.is_nighttime = nighttimeflag == 1  # Convert 0/1 flag to False/True flag
        self.is_daytime = daytimeflag == 1  # Convert 0/1 flag to False/True flag

    def calc(self, repeat: bool = True):
        """Calculate overall flag, based on individual flags from multiple iterations.

        Args:
            repeat: If *True*, the outlier detection is repeated until all
                outliers are removed.

        """

        self._overall_flag, n_iterations = self.repeat(self.run_flagtests, repeat=repeat)
        if self.showplot:
            self.defaultplot(n_iterations=n_iterations)

    def _flagtests(self, iteration) -> tuple[DatetimeIndex, DatetimeIndex, int]:
        """Perform tests required for this flag"""

        flag = pd.Series(index=self.filteredseries.index, data=np.nan)

        # Daytime
        s_daytime = self.filteredseries[self.is_daytime].copy()
        daytime_df = lof(series=s_daytime, n_neighbors=self.n_neighbors,
                         contamination=self.contamination, suffix="DAYTIME", n_jobs=self.n_jobs)
        ok_daytime = daytime_df['NOT_OUTLIER_DAYTIME'].dropna().index
        rejected_daytime = daytime_df['OUTLIER_DAYTIME'].dropna().index

        # Nighttime
        s_nighttime = self.filteredseries[self.is_nighttime].copy()
        nighttime_df = lof(series=s_nighttime, n_neighbors=self.n_neighbors,
                           contamination=self.contamination, suffix="NIGHTTIME")
        ok_nighttime = nighttime_df['NOT_OUTLIER_NIGHTTIME'].dropna().index
        rejected_nighttime = nighttime_df['OUTLIER_NIGHTTIME'].dropna().index

        # Collect daytime and nighttime flags in one overall flag
        flag.loc[ok_daytime] = 0
        flag.loc[rejected_daytime] = 2
        flag.loc[ok_nighttime] = 0
        flag.loc[rejected_nighttime] = 2

        # Collect data in dataframe
        df = pd.DataFrame(self.filteredseries)
        df = pd.concat([df, daytime_df, nighttime_df], axis=1)
        df['FLAG'] = flag

        df['CLEANED'] = df[self.filteredseries.name].copy()
        df['CLEANED'].loc[df['FLAG'] > 0] = np.nan

        n_outliers = (flag == 2).sum()

        ok = (flag == 0)
        ok = ok[ok].index
        rejected = (flag == 2)
        rejected = rejected[rejected].index

        if self.verbose:
            print(f"ITERATION#{iteration}")
            print(f"Total found outliers: {len(rejected_daytime)} values (daytime)")
            print(f"Total found outliers: {len(rejected_nighttime)} values (nighttime)")
            print(f"Total found outliers: {n_outliers} values (daytime+nighttime)")

        if self.showplot:
            self._plot(df=df)

        return ok, rejected, n_outliers

    def _plot(self, df: DataFrame):
        fig = plt.figure(facecolor='white', figsize=(12, 16))
        gs = gridspec.GridSpec(6, 1)  # rows, cols
        gs.update(wspace=0.3, hspace=0.1, left=0.05, right=0.95, top=0.95, bottom=0.05)
        ax_series = fig.add_subplot(gs[0, 0])
        ax_cleaned = fig.add_subplot(gs[1, 0], sharex=ax_series)
        ax_cleaned_daytime = fig.add_subplot(gs[2, 0], sharex=ax_series)
        ax_cleaned_nighttime = fig.add_subplot(gs[3, 0], sharex=ax_series)
        ax_daytime = fig.add_subplot(gs[4, 0], sharex=ax_series)
        ax_nighttime = fig.add_subplot(gs[5, 0], sharex=ax_series)

        ax_series.plot_date(x=df.index, y=df[self.filteredseries.name], fmt='o', mec='none',
                            alpha=.5, color='black', label="series")

        ax_cleaned.plot_date(x=df.index, y=df['CLEANED'], fmt='o', mec='none',
                             alpha=.5, label="cleaned series")

        ax_cleaned_daytime.plot_date(x=df.index, y=df['NOT_OUTLIER_DAYTIME'], fmt='o', mec='none',
                                     alpha=.5, label="cleaned daytime")

        ax_cleaned_nighttime.plot_date(x=df.index, y=df['NOT_OUTLIER_NIGHTTIME'], fmt='o', mec='none',
                                       alpha=.5, label="cleaned nighttime")

        ax_daytime.plot_date(x=df.index, y=df['NOT_OUTLIER_DAYTIME'], fmt='o', mec='none',
                             alpha=.5, label="OK daytime")
        ax_daytime.plot_date(x=df.index, y=df['OUTLIER_DAYTIME'], fmt='o', mec='none',
                             alpha=.5, color='red', label="outlier daytime")

        ax_nighttime.plot_date(x=df.index, y=df['NOT_OUTLIER_NIGHTTIME'], fmt='o', mec='none',
                               alpha=.5, label="OK nighttime")
        ax_nighttime.plot_date(x=df.index, y=df['OUTLIER_NIGHTTIME'], fmt='o', mec='none',
                               alpha=.5, color='red', label="outlier nighttime")

        default_format(ax=ax_series)
        default_format(ax=ax_cleaned)
        default_format(ax=ax_cleaned_daytime)
        default_format(ax=ax_cleaned_nighttime)
        default_format(ax=ax_daytime)
        default_format(ax=ax_nighttime)

        default_legend(ax=ax_series)
        default_legend(ax=ax_cleaned)
        default_legend(ax=ax_cleaned_daytime)
        default_legend(ax=ax_cleaned_nighttime)
        default_legend(ax=ax_daytime)
        default_legend(ax=ax_nighttime)

        plt.setp(ax_series.get_xticklabels(), visible=False)
        plt.setp(ax_cleaned.get_xticklabels(), visible=False)
        plt.setp(ax_cleaned_daytime.get_xticklabels(), visible=False)
        plt.setp(ax_cleaned_nighttime.get_xticklabels(), visible=False)
        plt.setp(ax_daytime.get_xticklabels(), visible=False)

        title = f"Outlier detection - local outlier factor"
        fig.suptitle(title, fontsize=theme.FIGHEADER_FONTSIZE)
        fig.show()
