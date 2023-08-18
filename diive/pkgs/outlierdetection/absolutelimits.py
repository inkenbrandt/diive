import numpy as np
import pandas as pd
from pandas import Series, DatetimeIndex

from diive.core.base.flagbase import FlagBase
from diive.core.times.times import DetectFrequency
from diive.core.utils.prints import ConsoleOutputDecorator
from diive.pkgs.createvar.daynightflag import nighttime_flag_from_latlon


@ConsoleOutputDecorator()
class AbsoluteLimitsDaytimeNighttime(FlagBase):
    """
    Generate flag that indicates if values in data are outside
    the specified range, defined by providing allowed minimum and
    maximum, separately for daytime and nighttime data


    Methods:
        calc(self, daytime_minmax: float, nighttime_minmax: float): Calculates flag

    After running calc, results can be accessed with:
        flag: Series
            Flag series where accepted (ok) values are indicated
            with flag=0, rejected values are indicated with flag=2
        filteredseries: Series
            Data with rejected values set to missing

    """
    flagid = 'OUTLIER_ABSLIM_DTNT'

    def __init__(self, series: Series, lat: float, lon: float,
                 timezone_of_timestamp: str, levelid: str = None):
        super().__init__(series=series, flagid=self.flagid, levelid=levelid)
        self.showplot = False
        self.verbose = False

        # Make sure time series has frequency
        # Freq is needed for the detection of daytime/nighttime from lat/lon
        if not self.series.index.freq:
            freq = DetectFrequency(index=self.series.index, verbose=True).get()
            self.series = self.series.asfreq(freq)

        # Detect nighttime
        self.is_nighttime = nighttime_flag_from_latlon(
            lat=lat, lon=lon, freq=self.series.index.freqstr,
            start=str(self.series.index[0]), stop=str(self.series.index[-1]),
            timezone_of_timestamp=timezone_of_timestamp, threshold_daytime=0)
        self.is_nighttime = self.is_nighttime == 1  # Convert 0/1 flag to False/True flag
        self.is_daytime = ~self.is_nighttime  # Daytime is inverse of nighttime

        # means_dt = []
        # sds_dt = []
        # means_nt = []
        # sds_nt = []
        # series_dt = self.series[self.is_daytime].dropna().copy()
        # series_nt = self.series[self.is_nighttime].dropna().copy()
        # for r in range(0, 100):
        #     ss_dt = series_dt.sample(n=int(len(series_dt)), replace=True, random_state=None)  # Bootstrap data
        #     ss_dt = ss_dt.sort_index()
        #     means_dt.append(ss_dt.mean())
        #     sds_dt.append(ss_dt.std())
        #
        #     ss_nt = series_nt.sample(n=int(len(series_nt)), replace=True, random_state=None)  # Bootstrap data
        #     ss_nt = ss_nt.sort_index()
        #     means_nt.append(ss_nt.quantile(.01))
        #     sds_nt.append(ss_nt.std())
        #
        # x_dt = np.median(means_dt)
        # y_dt = np.median(sds_dt) * 3
        # upper_dt = x_dt + y_dt
        # lower_dt = x_dt - y_dt
        #
        # x_nt = np.median(means_nt)
        # y_nt = np.median(sds_nt) * 3
        # upper_nt = x_nt + y_nt
        # lower_nt = x_nt - y_nt

    def calc(self, daytime_minmax: list[float, float], nighttime_minmax: list[float, float],
             showplot: bool = False, verbose: bool = False):
        """Calculate flag"""
        self.showplot = showplot
        self.verbose = verbose
        self.reset()
        ok, rejected = self._flagtests(daytime_minmax, nighttime_minmax)
        self.setflag(ok=ok, rejected=rejected)
        self.setfiltered(rejected=rejected)

    def _flagtests(self, daytime_minmax, nighttime_minmax) -> tuple[DatetimeIndex, DatetimeIndex]:
        """Perform tests required for this flag"""

        # Working data
        s = self.series.copy().dropna()
        flag = pd.Series(index=self.series.index, data=np.nan)

        # Run for daytime (dt)
        _s_dt = s[self.is_daytime].copy()  # Daytime data
        # _zscore_dt = funcs.zscore(series=_s_dt)
        _ok_dt = (_s_dt >= daytime_minmax[0]) & (_s_dt <= daytime_minmax[1])
        _ok_dt = _ok_dt[_ok_dt].index
        _rejected_dt = (_s_dt < daytime_minmax[0]) | (_s_dt > daytime_minmax[1])
        _rejected_dt = _rejected_dt[_rejected_dt].index

        # Run for nighttime (nt)
        _s_nt = s[self.is_nighttime].copy()  # Nighttime data
        _ok_nt = (_s_nt >= nighttime_minmax[0]) & (_s_nt <= nighttime_minmax[1])
        _ok_nt = _ok_nt[_ok_nt].index
        _rejected_nt = (_s_nt < nighttime_minmax[0]) | (_s_nt > nighttime_minmax[1])
        _rejected_nt = _rejected_nt[_rejected_nt].index

        # Collect daytime and nighttime flags in one overall flag
        flag.loc[_ok_dt] = 0
        flag.loc[_rejected_dt] = 2
        flag.loc[_ok_nt] = 0
        flag.loc[_rejected_nt] = 2

        total_outliers = (flag == 2).sum()

        ok = (flag == 0)
        ok = ok[ok].index
        rejected = (flag == 2)
        rejected = rejected[rejected].index

        if self.verbose:
            print(f"Total found outliers: {len(_rejected_dt)} values (daytime)")
            print(f"Total found outliers: {len(_rejected_nt)} values (nighttime)")
            print(f"Total found outliers: {total_outliers} values (daytime+nighttime)")

        if self.showplot:
            self.plot(ok, rejected,
                      plottitle=f"Outlier detection based on absolute limits for "
                                f"daytime {daytime_minmax} and nighttime {nighttime_minmax} "
                                f"absolute limits of {self.series.name}")

        return ok, rejected


@ConsoleOutputDecorator()
class AbsoluteLimits(FlagBase):
    """
    Generate flag that indicates if values in data are outside
    the specified range, defined by providing min, max in method
    ...

    Methods:
        calc(self, min: float, max: float): Calculates flag

    After running calc, results can be accessed with:
        flag: Series
            Flag series where accepted (ok) values are indicated
            with flag=0, rejected values are indicated with flag=2
        filteredseries: Series
            Data with rejected values set to missing

    """
    flagid = 'OUTLIER_ABSLIM'

    def __init__(self, series: Series, levelid: str = None):
        super().__init__(series=series, flagid=self.flagid, levelid=levelid)
        self.showplot = False
        self.verbose = False

    def calc(self, min: float, max: float, showplot: bool = False, verbose: bool = False):
        """Calculate flag"""
        self.showplot = showplot
        self.verbose = verbose
        self.reset()
        ok, rejected = self._flagtests(min, max)
        self.setflag(ok=ok, rejected=rejected)
        self.setfiltered(rejected=rejected)

    def _flagtests(self, min, max) -> tuple[DatetimeIndex, DatetimeIndex]:
        """Perform tests required for this flag"""
        ok = (self.series >= min) | (self.series <= max)
        ok = ok[ok].index
        rejected = (self.series < min) | (self.series > max)
        rejected = rejected[rejected].index
        if self.showplot: self.plot(ok=ok, rejected=rejected,
                                    plottitle=f"Outlier detection based on "
                                              f"absolute limits for {self.series.name}")
        return ok, rejected


def example():
    import numpy as np
    import pandas as pd
    np.random.seed(100)
    rows = 1000
    data = np.random.rand(rows) * 100  # Random numbers b/w 0 and 100
    tidx = pd.date_range('2019-01-01 00:30:00', periods=rows, freq='30T')
    series = pd.Series(data, index=tidx, name='TESTDATA')

    al = AbsoluteLimits(series=series, levelid='99')
    al.calc(min=16, max=84)

    print(series.describe())
    filteredseries = al.filteredseries
    print(filteredseries.describe())
    flag = al.flag
    print(flag.describe())


if __name__ == '__main__':
    example()
