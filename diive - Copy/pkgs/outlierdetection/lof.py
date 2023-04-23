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
from diive.pkgs.createvar.daynightflag import nighttime_flag_from_latlon


def lof(series: Series, n_neighbors: int = 20, contamination: float = 0.01, suffix: str = None):
    # Prepare data
    if not suffix: suffix = ""
    series = series.copy().dropna()
    ix = series.index
    vals = series.to_numpy().reshape(-1, 1)

    # Run analysis
    lof = LocalOutlierFactor(n_neighbors=n_neighbors,
                             algorithm='auto',
                             leaf_size=30,
                             metric='minkowski',
                             p=2,
                             metric_params=None,
                             contamination=contamination,
                             novelty=False,
                             n_jobs=-1)
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

    # Plot

    return df


@ConsoleOutputDecorator()
class LocalOutlierFactorAllData(FlagBase):
    """
    Identify outliers based on the local outlier factor
    ...

    Methods:
        calc(factor: float = 4): Calculates flag

    After running calc(), results can be accessed with:
        flag: Series
            Flag series where accepted (ok) values are indicated
            with flag=0, rejected values are indicated with flag=2
        filteredseries: Series
            Data with rejected values set to missing

    Kudos:
    - https://scikit-learn.org/stable/modules/outlier_detection.html
    - https://scikit-learn.org/stable/auto_examples/neighbors/plot_lof_outlier_detection.html#sphx-glr-auto-examples-neighbors-plot-lof-outlier-detection-py
    - https://www.datatechnotes.com/2020/04/anomaly-detection-with-local-outlier-factor-in-python.html

    """
    flagid = 'OUTLIER_LOF'

    def __init__(self, series: Series, levelid: str = None):
        super().__init__(series=series, flagid=self.flagid, levelid=levelid)
        self.showplot = False
        self.verbose = False

    def calc(self, n_neighbors: int = 20, contamination: float = 0.01, showplot: bool = False, verbose: bool = False):
        """Calculate flag"""
        self.showplot = showplot
        self.verbose = verbose
        self.reset()
        ok, rejected = self._flagtests(n_neighbors=n_neighbors, contamination=contamination)
        self.setflag(ok=ok, rejected=rejected)
        self.setfiltered(rejected=rejected)

    def _flagtests(self, n_neighbors: int = 20, contamination: float = 0.01) -> tuple[DatetimeIndex, DatetimeIndex]:
        """Perform tests required for this flag"""

        flag = pd.Series(index=self.series.index, data=np.nan)

        s = self.series.copy()
        _df = lof(series=s, n_neighbors=n_neighbors,
                  contamination=contamination, suffix="")
        ok = _df['NOT_OUTLIER_'].dropna().index
        rejected = _df['OUTLIER_'].dropna().index

        # Collect daytime and nighttime flags in one overall flag
        flag.loc[ok] = 0
        flag.loc[rejected] = 2

        # Collect data in dataframe
        df = pd.DataFrame(self.series)
        df = pd.concat([df, _df], axis=1)
        df['FLAG'] = flag

        df['CLEANED'] = df[self.series.name].copy()
        df['CLEANED'].loc[df['FLAG'] > 0] = np.nan

        total_outliers = (flag == 2).sum()

        ok = (flag == 0)
        ok = ok[ok].index
        rejected = (flag == 2)
        rejected = rejected[rejected].index

        if self.verbose:
            print(f"Total found outliers: {len(rejected)} values (daytime)")
            print(f"Total found outliers: {total_outliers} values (daytime+nighttime)")

        if self.showplot: self._plot(df=df)

        return ok, rejected

    def _plot(self, df: DataFrame):
        fig = plt.figure(facecolor='white', figsize=(12, 16))
        gs = gridspec.GridSpec(3, 1)  # rows, cols
        gs.update(wspace=0.3, hspace=0.1, left=0.05, right=0.95, top=0.95, bottom=0.05)
        ax1 = fig.add_subplot(gs[0, 0])
        ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
        ax3 = fig.add_subplot(gs[2, 0], sharex=ax1)

        ax1.plot_date(x=df.index, y=df[self.series.name], marker='o', mec='none',
                      alpha=.5, color='black', label="series")

        ax2.plot_date(x=df.index, y=df['CLEANED'], marker='o', mec='none',
                      alpha=.5, label="cleaned series")

        ax3.plot_date(x=df.index, y=df['NOT_OUTLIER_'], marker='o', mec='none',
                      alpha=.5, label="OK daytime")
        ax3.plot_date(x=df.index, y=df['OUTLIER_'], marker='o', mec='none',
                      alpha=.5, color='red', label="outlier daytime")

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
    """
    Identify outliers based on the local outlier factor, done separately for
    daytime and nighttime data
    ...

    Methods:
        calc(factor: float = 4): Calculates flag

    After running calc(), results can be accessed with:
        flag: Series
            Flag series where accepted (ok) values are indicated
            with flag=0, rejected values are indicated with flag=2
        filteredseries: Series
            Data with rejected values set to missing

    Kudos:
    - https://scikit-learn.org/stable/modules/outlier_detection.html
    - https://scikit-learn.org/stable/auto_examples/neighbors/plot_lof_outlier_detection.html#sphx-glr-auto-examples-neighbors-plot-lof-outlier-detection-py
    - https://www.datatechnotes.com/2020/04/anomaly-detection-with-local-outlier-factor-in-python.html

    """
    flagid = 'OUTLIER_LOFDTNT'

    def __init__(self, series: Series, site_lat: float, site_lon: float, levelid: str = None):
        super().__init__(series=series, flagid=self.flagid, levelid=levelid)
        self.showplot = False
        self.verbose = False

        # Detect nighttime
        self.is_nighttime = nighttime_flag_from_latlon(
            lat=site_lat, lon=site_lon, freq=self.series.index.freqstr,
            start=str(self.series.index[0]), stop=str(self.series.index[-1]),
            timezone_of_timestamp='UTC+01:00', threshold_daytime=0)
        self.is_nighttime = self.is_nighttime == 1  # Convert 0/1 flag to False/True flag
        self.is_daytime = ~self.is_nighttime  # Daytime is inverse of nighttime

    def calc(self, n_neighbors: int = 20, contamination: float = 0.01, showplot: bool = False, verbose: bool = False):
        """Calculate flag"""
        self.showplot = showplot
        self.verbose = verbose
        self.reset()
        ok, rejected = self._flagtests(n_neighbors=n_neighbors, contamination=contamination)
        self.setflag(ok=ok, rejected=rejected)
        self.setfiltered(rejected=rejected)

    def _flagtests(self, n_neighbors: int = 20, contamination: float = 0.01) -> tuple[DatetimeIndex, DatetimeIndex]:
        """Perform tests required for this flag"""

        flag = pd.Series(index=self.series.index, data=np.nan)

        # Daytime
        s_daytime = self.series[self.is_daytime].copy()
        daytime_df = lof(series=s_daytime, n_neighbors=n_neighbors,
                         contamination=contamination, suffix="DAYTIME")
        ok_daytime = daytime_df['NOT_OUTLIER_DAYTIME'].dropna().index
        rejected_daytime = daytime_df['OUTLIER_DAYTIME'].dropna().index

        # Nighttime
        s_nighttime = self.series[self.is_nighttime].copy()
        nighttime_df = lof(series=s_nighttime, n_neighbors=n_neighbors,
                                contamination=contamination, suffix="NIGHTTIME")
        ok_nighttime = nighttime_df['NOT_OUTLIER_NIGHTTIME'].dropna().index
        rejected_nighttime = nighttime_df['OUTLIER_NIGHTTIME'].dropna().index

        # Collect daytime and nighttime flags in one overall flag
        flag.loc[ok_daytime] = 0
        flag.loc[rejected_daytime] = 2
        flag.loc[ok_nighttime] = 0
        flag.loc[rejected_nighttime] = 2

        # Collect data in dataframe
        df = pd.DataFrame(self.series)
        df = pd.concat([df, daytime_df, nighttime_df], axis=1)
        df['FLAG'] = flag

        df['CLEANED'] = df[self.series.name].copy()
        df['CLEANED'].loc[df['FLAG'] > 0] = np.nan

        total_outliers = (flag == 2).sum()

        ok = (flag == 0)
        ok = ok[ok].index
        rejected = (flag == 2)
        rejected = rejected[rejected].index

        if self.verbose:
            print(f"Total found outliers: {len(rejected_daytime)} values (daytime)")
            print(f"Total found outliers: {len(rejected_nighttime)} values (nighttime)")
            print(f"Total found outliers: {total_outliers} values (daytime+nighttime)")

        if self.showplot: self._plot(df=df)

        return ok, rejected

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

        ax_series.plot_date(x=df.index, y=df[self.series.name], marker='o', mec='none',
                            alpha=.5, color='black', label="series")

        ax_cleaned.plot_date(x=df.index, y=df['CLEANED'], marker='o', mec='none',
                             alpha=.5, label="cleaned series")

        ax_cleaned_daytime.plot_date(x=df.index, y=df['NOT_OUTLIER_DAYTIME'], marker='o', mec='none',
                                     alpha=.5, label="cleaned daytime")

        ax_cleaned_nighttime.plot_date(x=df.index, y=df['NOT_OUTLIER_NIGHTTIME'], marker='o', mec='none',
                                       alpha=.5, label="cleaned nighttime")

        ax_daytime.plot_date(x=df.index, y=df['NOT_OUTLIER_DAYTIME'], marker='o', mec='none',
                             alpha=.5, label="OK daytime")
        ax_daytime.plot_date(x=df.index, y=df['OUTLIER_DAYTIME'], marker='o', mec='none',
                             alpha=.5, color='red', label="outlier daytime")

        ax_nighttime.plot_date(x=df.index, y=df['NOT_OUTLIER_NIGHTTIME'], marker='o', mec='none',
                               alpha=.5, label="OK nighttime")
        ax_nighttime.plot_date(x=df.index, y=df['OUTLIER_NIGHTTIME'], marker='o', mec='none',
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
